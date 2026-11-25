import Foundation
import Network
import UIKit

final class WellnessAPI:NSObject,URLSessionDataDelegate,URLSessionTaskDelegate,@unchecked Sendable {
    let store:EventStore
    var onStatus:((String)->Void)?;var onUpload:(()->Void)?;var onAcknowledged:(()->Void)?
    @MainActor private var backgroundCompletion:(()->Void)?
    @MainActor private var backgroundEventsFinished=false
    private var bodies:[Int:Data]=[:];private var preparing=false;private var foreground=false
    private var nextForegroundAt:TimeInterval=0;private var retryWork:DispatchWorkItem?
    private var bluetoothLiveUntil:TimeInterval=0;private var lastHTTP:Int?
    private var lastDiagnosticWrite:TimeInterval=0
    private var fastNetwork=false
    private var lastBatchEvents:Int?;private var lastPreparationMS:Double?;private var lastRequestMS:Double?;private var lastAcknowledgementMS:Double?
    private var stopped=false
    private let diagnosticQueue=DispatchQueue(label:"HomePulse.transport-diagnostics",qos:.utility)
    private let mutex=NSLock();private let monitor=NWPathMonitor()
    private let folder:URL
    private let transport:URLSession?;private let credentialsProvider:()->Credentials?;private let monitorNetwork:Bool
    private lazy var live:URLSession = {
        if let transport{return transport}
        let c=URLSessionConfiguration.ephemeral
        c.waitsForConnectivity=false;c.timeoutIntervalForRequest=12;c.timeoutIntervalForResource=20
        return URLSession(configuration:c)
    }()
    private lazy var background:URLSession = {
        let c=URLSessionConfiguration.background(withIdentifier:"com.familos.homepulse.uploads")
        c.waitsForConnectivity=true;c.isDiscretionary=false;c.sessionSendsLaunchEvents=true
        c.timeoutIntervalForRequest=60;c.timeoutIntervalForResource=86400
        let q=OperationQueue();q.maxConcurrentOperationCount=1
        return URLSession(configuration:c,delegate:self,delegateQueue:q)
    }()
    init(store:EventStore,folder:URL,transport:URLSession?=nil,credentials:(()->Credentials?)?=nil,monitorNetwork:Bool=true,fastNetwork:Bool=false){self.store=store;self.folder=folder;self.transport=transport;self.credentialsProvider=credentials ?? {SecureStore.credentials()};self.monitorNetwork=monitorNetwork;self.fastNetwork=fastNetwork;super.init()
        monitor.pathUpdateHandler={[weak self] path in
            guard let self else{return}
            self.mutex.lock();self.fastNetwork=(path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet)) && !path.isConstrained;self.mutex.unlock()
            if path.status == .satisfied{self.upload()}
        }
        if monitorNetwork{monitor.start(queue:DispatchQueue(label:"HomePulse.network"));_ = background}
    }
    deinit{monitor.cancel();retryWork?.cancel()}
    #if DEBUG
    private func stopTestRetries(){mutex.lock();stopped=true;retryWork?.cancel();mutex.unlock()}
    func stopForTesting()async{
        stopTestRetries()
        await withCheckedContinuation{continuation in diagnosticQueue.async{continuation.resume()}}
    }
    #endif
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
    func setForeground(_ active:Bool){
        mutex.lock();let changed=foreground != active;foreground=active;if changed{nextForegroundAt=0};mutex.unlock()
        if active && changed && monitorNetwork {
            // Replay the same durable batch through the immediate session. A
            // cancelled background request can still ACK; IDs remain idempotent.
            background.getAllTasks{tasks in tasks.forEach{$0.cancel()}}
        }
        if !active && changed && !useImmediateTransport{live.getAllTasks{tasks in tasks.forEach{$0.cancel()}}}
        upload()
    }
    // Each actual BLE live notification grants only a short send window.
    // Background URLSession remains the durable fallback, not the live clock.
    func noteBluetoothActivity(){
        mutex.lock();bluetoothLiveUntil=Date().timeIntervalSince1970+5;mutex.unlock()
        upload()
    }
    private var useImmediateTransport:Bool{mutex.lock();defer{mutex.unlock()};return foreground || Date().timeIntervalSince1970<bluetoothLiveUntil}
    private var batchLimit:Int{mutex.lock();defer{mutex.unlock()};return fastNetwork ? 1000:128}
    private var acknowledgedDelay:Double{mutex.lock();defer{mutex.unlock()};return fastNetwork ? 0:1}
    func upload(){
        mutex.lock();let immediate=foreground || Date().timeIntervalSince1970<bluetoothLiveUntil;if stopped || preparing || immediate && Date().timeIntervalSince1970<nextForegroundAt{mutex.unlock();return};preparing=true;mutex.unlock()
        guard let credentials=credentialsProvider() else{finishPreparation();return}
        if immediate{uploadForeground(credentials);return}
        background.getAllTasks{[weak self] tasks in
            guard let self else{return}
            if self.useImmediateTransport{self.finishPreparation();self.upload();return}
            defer{self.finishPreparation()}
            guard tasks.isEmpty else{return}
            do {
                guard let batch=try self.store.prepareBatch(limit:self.batchLimit) else {self.status("Sincronizzato");return}
                try FileManager.default.createDirectory(at:self.folder,withIntermediateDirectories:true)
                let file=self.folder.appendingPathComponent(batch.batch_id+".json")
                try JSONEncoder().encode(batch).write(to:file,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])
                let task=self.background.uploadTask(with:try self.eventRequest(credentials),fromFile:file)
                task.taskDescription=batch.batch_id;task.resume();self.status("Invio di \(batch.events.count) eventi")
            } catch {self.status("Offline: eventi conservati sul telefono")}
        }
    }
    private func eventRequest(_ credentials:Credentials)throws->URLRequest{
        var r=URLRequest(url:try Self.base(credentials.server).appendingPathComponent("bridges/events"));r.httpMethod="POST"
        r.setValue("application/json",forHTTPHeaderField:"Content-Type");r.setValue("Bearer \(credentials.access_token)",forHTTPHeaderField:"Authorization")
        return r
    }
    private func uploadForeground(_ credentials:Credentials){
        Task{@MainActor [weak self] in await self?.startImmediateUpload(credentials)}
    }
    @MainActor private func startImmediateUpload(_ credentials:Credentials)async{
        let execution=ShortBackgroundExecution(name:"HomePulse live upload")
        do{
            let limit=batchLimit,preparationStarted=Date()
            guard let batch=try await store.perform({try $0.prepareBatch(limit:limit,prioritizeLive:true)}) else{execution.finish();finishPreparation();status("Live collegato a FamilOS");retry(after:2);return}
            let bytes=try await Task.detached(priority:.utility){try JSONEncoder().encode(batch)}.value
            recordPreparation(events:batch.events.count,milliseconds:Date().timeIntervalSince(preparationStarted)*1000)
            var request=try eventRequest(credentials);request.httpBody=bytes
            let requestStarted=Date()
            let task=live.dataTask(with:request){[weak self] data,response,error in
                defer{Task{@MainActor in execution.finish()}}
                guard let self else{return};var delay=3.0
                defer{self.finishPreparation();self.onUpload?();self.retry(after:delay)}
                do{
                    if let error{self.status("Invio live: \(error.localizedDescription)");return}
                    guard let http=response as? HTTPURLResponse else{throw AppError.message("Risposta server assente")}
                    guard (200...299).contains(http.statusCode) else{self.status("Invio rifiutato dal server: HTTP \(http.statusCode)",http:http.statusCode);return}
                    guard let data else{throw AppError.message("Conferma server assente")}
                    self.recordRequest(milliseconds:Date().timeIntervalSince(requestStarted)*1000)
                    let partial=try self.accept(data,batch:batch.batch_id)
                    delay=partial ? 10:self.acknowledgedDelay
                }catch{self.status("Offline: eventi live conservati sul telefono")}
            }
            task.priority=URLSessionTask.highPriority;task.resume()
        }catch{execution.finish();finishPreparation();status("Invio live rinviato: coda locale intatta");retry(after:3)}
    }
    private func retry(after delay:Double){
        mutex.lock();guard !stopped else{mutex.unlock();return};if foreground || Date().timeIntervalSince1970<bluetoothLiveUntil{nextForegroundAt=Date().timeIntervalSince1970+delay}
        retryWork?.cancel();let work=DispatchWorkItem{[weak self] in self?.upload()};retryWork=work;mutex.unlock()
        DispatchQueue.global().asyncAfter(deadline:.now()+delay,execute:work)
    }
    private func status(_ message:String,http:Int?=nil){
        mutex.lock();guard !stopped else{mutex.unlock();return};if let http{lastHTTP=http}
        let code=lastHTTP,active=foreground,now=Date().timeIntervalSince1970
        let fast=fastNetwork,batchEvents=lastBatchEvents,prepare=lastPreparationMS,request=lastRequestMS,ack=lastAcknowledgementMS
        let writeDiagnostic=now-lastDiagnosticWrite>=5 || (http != nil && !(200...299).contains(http!))
        if writeDiagnostic{lastDiagnosticWrite=now};mutex.unlock()
        onStatus?(message)
        guard writeDiagnostic else{return}
        // Strictly technical diagnostics: no sensor values, IDs or credentials.
        let path=folder.deletingLastPathComponent().appendingPathComponent("transport-status.json")
        let store=store
        diagnosticQueue.async{
            var value:[String:Any]=["status":message,"foreground":active,"journal_events":store.journalCount,"pending_events":store.pendingCount,"at":Date().timeIntervalSince1970]
            if let code{value["last_http_status"]=code}
            value["fast_network"]=fast
            if let batchEvents{value["batch_events"]=batchEvents}
            if let prepare{value["preparation_ms"]=prepare}
            if let request{value["request_ms"]=request}
            if let ack{value["acknowledgement_ms"]=ack}
            if let data=try? JSONSerialization.data(withJSONObject:value){try? data.write(to:path,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])}
        }
    }
    private func finishPreparation(){mutex.lock();preparing=false;mutex.unlock()}
    private func recordPreparation(events:Int,milliseconds:Double){mutex.lock();lastBatchEvents=events;lastPreparationMS=milliseconds;mutex.unlock()}
    private func recordRequest(milliseconds:Double){mutex.lock();lastRequestMS=milliseconds;mutex.unlock()}
    @discardableResult private func accept(_ data:Data,batch:String)throws->Bool{
        guard data.count<=1_000_000 else{throw AppError.message("Conferma troppo grande")}
        let result=try JSONDecoder().decode(JSONValue.self,from:data)
        guard result["batch_id"].string==batch,case .array(let values)=result["accepted_event_ids"],values.allSatisfy({$0.string != nil}) else{throw AppError.message("Conferma batch non valida")}
        let started=Date(),complete=try store.acknowledge(batchID:batch,accepted:values.compactMap{$0.string})
        mutex.lock();lastAcknowledgementMS=Date().timeIntervalSince(started)*1000;mutex.unlock()
        try? FileManager.default.removeItem(at:folder.appendingPathComponent(batch+".json"))
        status("Live inviato a FamilOS",http:200);onUpload?();onAcknowledged?()
        return !complete
    }
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive data:Data){
        var value=bodies[dataTask.taskIdentifier] ?? Data();if value.count+data.count<=1_000_000{value.append(data)}
        bodies[dataTask.taskIdentifier]=value
    }
    func urlSession(_ session:URLSession,task:URLSessionTask,didCompleteWithError error:Error?){
        let data=bodies.removeValue(forKey:task.taskIdentifier) ?? Data()
        guard let batch=task.taskDescription,error==nil,let response=task.response as? HTTPURLResponse,(200...299).contains(response.statusCode) else {
            status("Invio rinviato: coda locale intatta");onUpload?();retry(after:3);return
        }
        do{let partial=try accept(data,batch:batch);retry(after:partial ? 10:0.1)}
        catch{status("Conferma non verificabile: nessun dato eliminato");onUpload?();retry(after:10)}
    }
    @MainActor func attachBackgroundCompletion(_ completion:@escaping()->Void){
        if backgroundEventsFinished{backgroundEventsFinished=false;completion()}
        else{backgroundCompletion=completion}
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session:URLSession){Task{@MainActor [weak self] in
        guard let self else{return}
        if let completion=backgroundCompletion{backgroundCompletion=nil;completion()}
        else{backgroundEventsFinished=true}
    }}
}


/// Protects one short network request from suspension; no perpetual runtime.
@MainActor final class ShortBackgroundExecution {
    private var identifier:UIBackgroundTaskIdentifier = .invalid
    init(name:String){
        if UIApplication.shared.applicationState != .active{
            identifier=UIApplication.shared.beginBackgroundTask(withName:name){[weak self] in
                Task{@MainActor in self?.finish()}
            }
        }
    }
    func finish(){
        guard identifier != .invalid else{return}
        let task=identifier;identifier = .invalid;UIApplication.shared.endBackgroundTask(task)
    }
}
