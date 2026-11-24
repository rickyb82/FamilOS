import Foundation
import Network

final class WellnessAPI:NSObject,URLSessionDataDelegate,URLSessionTaskDelegate,@unchecked Sendable {
    let store:EventStore
    var onStatus:((String)->Void)?;var onUpload:(()->Void)?;var onAcknowledged:(()->Void)?
    var backgroundCompletion:(()->Void)?
    private var bodies:[Int:Data]=[:];private var preparing=false
    private let mutex=NSLock();private let monitor=NWPathMonitor()
    private let folder:URL
    private lazy var background:URLSession = {
        let c=URLSessionConfiguration.background(withIdentifier:"com.familos.homepulse.uploads")
        c.waitsForConnectivity=true;c.isDiscretionary=false;c.sessionSendsLaunchEvents=true
        c.timeoutIntervalForRequest=60;c.timeoutIntervalForResource=86400
        let q=OperationQueue();q.maxConcurrentOperationCount=1
        return URLSession(configuration:c,delegate:self,delegateQueue:q)
    }()
    init(store:EventStore,folder:URL){self.store=store;self.folder=folder;super.init()
        monitor.pathUpdateHandler={[weak self] path in if path.status == .satisfied {self?.upload()}}
        monitor.start(queue:DispatchQueue(label:"HomePulse.network"));_ = background
    }
    static func base(_ url:URL)throws->URL {
        guard url.scheme=="https",url.host != nil,url.user==nil,url.password==nil else{throw AppError.message("Serve un indirizzo server HTTPS valido")}
        guard var c=URLComponents(url:url,resolvingAgainstBaseURL:false) else{throw AppError.message("Indirizzo server non valido")}
        c.query=nil;c.fragment=nil
        if c.path.isEmpty || c.path=="/" {c.path="/api/wellness/v1"}
        while c.path.hasSuffix("/"){c.path.removeLast()}
        guard let result=c.url else{throw AppError.message("Indirizzo server non valido")};return result
    }
    func request(path:String,method:String="GET",body:JSONValue?=nil,credentials:Credentials?=SecureStore.credentials(),base:URL?=nil,query:[URLQueryItem]?=nil) async throws->JSONValue {
        guard let endpoint=try (base ?? credentials?.server).map(Self.base) else{throw AppError.message("Associa prima FamilOS")}
        var components=URLComponents(url:endpoint.appendingPathComponent(path),resolvingAgainstBaseURL:false)!;components.queryItems=query
        var r=URLRequest(url:components.url!);r.httpMethod=method;r.timeoutInterval=25
        r.setValue("application/json",forHTTPHeaderField:"Content-Type")
        if let token=credentials?.access_token {r.setValue("Bearer \(token)",forHTTPHeaderField:"Authorization")}
        if let body {r.httpBody=try JSONEncoder().encode(body)}
        let (data,response)=try await URLSession.shared.data(for:r)
        guard let http=response as? HTTPURLResponse,(200...299).contains(http.statusCode) else {throw AppError.message("Server non disponibile o associazione scaduta; dati offline conservati")}
        guard data.count<10_000_000 else{throw AppError.message("Risposta server oltre il limite")}
        return try JSONDecoder().decode(JSONValue.self,from:data)
    }
    func upload(){
        mutex.lock();if preparing{mutex.unlock();return};preparing=true;mutex.unlock()
        guard let credentials=SecureStore.credentials() else{finishPreparation();return}
        background.getAllTasks{[weak self] tasks in
            guard let self else{return};defer{self.finishPreparation()}
            guard tasks.isEmpty else{return}
            do {
                guard let batch=try self.store.prepareBatch() else {self.onStatus?("Sincronizzato");return}
                try FileManager.default.createDirectory(at:self.folder,withIntermediateDirectories:true)
                let file=self.folder.appendingPathComponent(batch.batch_id+".json")
                try JSONEncoder().encode(batch).write(to:file,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])
                let base=try Self.base(credentials.server)
                var r=URLRequest(url:base.appendingPathComponent("bridges/events"));r.httpMethod="POST"
                r.setValue("application/json",forHTTPHeaderField:"Content-Type")
                r.setValue("Bearer \(credentials.access_token)",forHTTPHeaderField:"Authorization")
                let task=self.background.uploadTask(with:r,fromFile:file);task.taskDescription=batch.batch_id;task.resume()
                self.onStatus?("Invio di \(batch.events.count) eventi")
            } catch {self.onStatus?("Offline: eventi conservati sul telefono")}
        }
    }
    private func finishPreparation(){mutex.lock();preparing=false;mutex.unlock()}
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive data:Data){
        var value=bodies[dataTask.taskIdentifier] ?? Data()
        if value.count+data.count<=1_000_000{value.append(data)}
        bodies[dataTask.taskIdentifier]=value
    }
    func urlSession(_ session:URLSession,task:URLSessionTask,didCompleteWithError error:Error?){
        let data=bodies.removeValue(forKey:task.taskIdentifier) ?? Data()
        guard let batch=task.taskDescription,error==nil,let response=task.response as? HTTPURLResponse,(200...299).contains(response.statusCode) else {
            onStatus?("Invio rinviato: coda locale intatta");onUpload?();return
        }
        do{
            let result=try JSONDecoder().decode(JSONValue.self,from:data)
            guard result["batch_id"].string==batch else{throw AppError.message("Conferma batch non valida")}
            let values=result["accepted_event_ids"].array
            guard values.allSatisfy({$0.string != nil}) else{throw AppError.message("Conferma eventi non valida")}
            try store.acknowledge(batchID:batch,accepted:values.compactMap{$0.string})
            try? FileManager.default.removeItem(at:folder.appendingPathComponent(batch+".json"))
            onStatus?("Sincronizzazione confermata");onUpload?();onAcknowledged?()
            // Una conferma parziale viene ritentata con backoff, non in un loop.
            DispatchQueue.global().asyncAfter(deadline:.now()+10){[weak self] in self?.upload()}
        }catch{onStatus?("Conferma non verificabile: nessun dato eliminato");onUpload?()}
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session:URLSession){DispatchQueue.main.async{[weak self] in self?.backgroundCompletion?();self?.backgroundCompletion=nil}}
}
