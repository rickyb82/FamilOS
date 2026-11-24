import Foundation
import CoreBluetooth
import Combine

@MainActor final class RingBridge:NSObject,ObservableObject,@preconcurrency CBCentralManagerDelegate,@preconcurrency CBPeripheralDelegate {
    @Published var status="Anello scollegato" {didSet{if status != oldValue{try? store.append(kind:"device_status",payload:.object(["source":.string("iphone_bluetooth"),"status":.string(status)]));onJournal?()}}}
    @Published var devices:[CBPeripheral]=[]
    @Published var connected=false;@Published var ready=false;@Published var busy=false
    @Published var apiVersion:[Int]=[];@Published var capabilities:[Int:Int]=[:]
    @Published var exerciseRaw:JSONValue = .null
    @Published var batteryLevel:Int?;@Published var batteryAt:Date?
    @Published var lastConnectionError:String?
    var onBattery:((Int,Date)->Void)?
    var onLive:((OuraLive,Date)->Void)?;var onHistory:((Data)->Void)?;var onJournal:(()->Void)?
    private var central:CBCentralManager!;private var peripheral:CBPeripheral?
    private var writeCharacteristic:CBCharacteristic?;private var notifyCharacteristic:CBCharacteristic?
    private var connectionTimeout:Task<Void,Never>?;private var connectionFailure:String?
    private var desired=false;private var authenticating=false;private var pairingAttempt=false
    private var logicalRing:String?;private var framer=OuraFramer()
    private var continuation:CheckedContinuation<Data,Error>?
    private var responseMatch:((Data)->Bool)?;private var timeoutTask:Task<Void,Never>?
    private struct SportControl:Codable{var restoreMode:UInt8;var pendingStop:Bool}
    private var sportKey:String{"sport.control.\(logicalRing ?? "unbound")"}
    private var nextHistoryAt=Date.distantPast;private var nextExerciseAt=Date.distantPast;private var nextBatteryAt=Date.distantPast
    private var pageRecords:[Data]?;private let store:EventStore
    init(store:EventStore){self.store=store;super.init();logicalRing=UserDefaults.standard.string(forKey:"ringLogicalID");desired=UserDefaults.standard.bool(forKey:"ringWantsConnection");central=CBCentralManager(delegate:self,queue:.main,options:[CBCentralManagerOptionRestoreIdentifierKey:"com.familos.homepulse.central",CBCentralManagerOptionShowPowerAlertKey:true])}
    func configure(logicalRing:String){self.logicalRing=logicalRing;UserDefaults.standard.set(logicalRing,forKey:"ringLogicalID")}
    var hasOwnerKey:Bool {guard let id=logicalRing else{return false};return SecureStore.read("ring.\(id)")?.count==16}
    func scan(){guard central.state == .poweredOn else{status="Attiva il Bluetooth sull’iPhone";return};guard hasOwnerKey else{status="Attendi l’associazione dell’anello da FamilOS";return};devices=[];central.scanForPeripherals(withServices:[CBUUID(string:OuraWire.service)],options:nil);status="Ricerca anello…";Task{try? await Task.sleep(nanoseconds:15_000_000_000);central.stopScan()}}
    func connect(_ device:CBPeripheral){connectionTimeout?.cancel();connectionFailure=nil;guard hasOwnerKey else{status="Credenziale anello non ancora configurata";return};desired=true;UserDefaults.standard.set(true,forKey:"ringWantsConnection");peripheral=device;device.delegate=self;central.stopScan();UserDefaults.standard.set(device.identifier.uuidString,forKey:"ringPeripheral");central.connect(device,options:[CBConnectPeripheralOptionNotifyOnDisconnectionKey:true]);status="Connessione…"
        if device.state == .connected{centralManager(central,didConnect:device);return}
        connectionTimeout=Task{try? await Task.sleep(nanoseconds:30_000_000_000);guard !Task.isCancelled,peripheral===device,!connected else{return};connectionFailure="Collegamento non completato entro 30 secondi. Avvicina l’anello e riprova.";lastConnectionError=connectionFailure;status=connectionFailure!;central.cancelPeripheralConnection(device)}
    }
    func disconnect(){connectionTimeout?.cancel();connectionFailure=nil;desired=false;UserDefaults.standard.set(false,forKey:"ringWantsConnection")
        guard let peripheral else{return}
        guard ready,!busy else{ready=false;central.cancelPeripheralConnection(peripheral);return}
        busy=true;Task{defer{ready=false;busy=false;central.cancelPeripheralConnection(peripheral)}
            try? OuraWire.ack(await exchange(Data([0x2f,3,0x22,2,1]),tag:0x2f,extended:0x23,seconds:3),extended:0x23,feature:2)
            try? OuraWire.ack(await exchange(Data([0x2f,3,0x26,2,0]),tag:0x2f,extended:0x27,seconds:3),extended:0x27,feature:2)
        }
    }
    func centralManagerDidUpdateState(_ central:CBCentralManager){
        if central.state != .poweredOn {status=central.state == .unauthorized ? "Autorizza Bluetooth nelle impostazioni":"Bluetooth non disponibile";return}
        if desired,hasOwnerKey {
            if peripheral==nil,let text=UserDefaults.standard.string(forKey:"ringPeripheral"),let id=UUID(uuidString:text){peripheral=central.retrievePeripherals(withIdentifiers:[id]).first;peripheral?.delegate=self}
            if let peripheral{connect(peripheral)}
        }
    }
    func centralManager(_ central:CBCentralManager,willRestoreState dict:[String:Any]){
        logicalRing=UserDefaults.standard.string(forKey:"ringLogicalID")
        if let values=dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral],let p=values.first {peripheral=p;p.delegate=self;desired=true;if p.state == .connected {connected=true;p.discoverServices([CBUUID(string:OuraWire.service)])}}
    }
    func centralManager(_ central:CBCentralManager,didDiscover peripheral:CBPeripheral,advertisementData:[String:Any],rssi RSSI:NSNumber){if !devices.contains(where:{$0.identifier==peripheral.identifier}){devices.append(peripheral)}}
    func centralManager(_ central:CBCentralManager,didConnect peripheral:CBPeripheral){connectionTimeout?.cancel();connectionFailure=nil;lastConnectionError=nil;connected=true;ready=false;pairingAttempt=false;framer=OuraFramer();peripheral.delegate=self;peripheral.discoverServices([CBUUID(string:OuraWire.service)]);status="Verifica servizi anello…"}
    func centralManager(_ central:CBCentralManager,didFailToConnect peripheral:CBPeripheral,error:Error?){connectionFailure=error.map{"Connessione non riuscita: \($0.localizedDescription)"};lastConnectionError=connectionFailure;lost()}
    func centralManager(_ central:CBCentralManager,didDisconnectPeripheral peripheral:CBPeripheral,error:Error?){lost()}
    private func lost(){connectionTimeout?.cancel();connected=false;ready=false;writeCharacteristic=nil;notifyCharacteristic=nil;busy=false;authenticating=false;failWaiter(AppError.message("Collegamento anello interrotto"));status=connectionFailure ?? "Anello scollegato";if desired,let peripheral{Task{try? await Task.sleep(nanoseconds:5_000_000_000);if desired,central.state == .poweredOn{connect(peripheral)}}}}
    func peripheral(_ peripheral:CBPeripheral,didDiscoverServices error:Error?){guard error==nil,let service=peripheral.services?.first(where:{$0.uuid==CBUUID(string:OuraWire.service)})else{status="Servizio Oura non disponibile";return};peripheral.discoverCharacteristics([CBUUID(string:OuraWire.write),CBUUID(string:OuraWire.notify)],for:service)}
    func peripheral(_ peripheral:CBPeripheral,didDiscoverCharacteristicsFor service:CBService,error:Error?){
        guard error==nil else{status="Caratteristiche Bluetooth non disponibili";return}
        writeCharacteristic=service.characteristics?.first{$0.uuid==CBUUID(string:OuraWire.write)}
        notifyCharacteristic=service.characteristics?.first{$0.uuid==CBUUID(string:OuraWire.notify)}
        guard writeCharacteristic != nil,let notifyCharacteristic else{status="Caratteristiche Oura mancanti";return}
        if notifyCharacteristic.isNotifying{Task{await handshake()}}else{peripheral.setNotifyValue(true,for:notifyCharacteristic)}
    }
    func peripheral(_ peripheral:CBPeripheral,didUpdateNotificationStateFor characteristic:CBCharacteristic,error:Error?){
        if error != nil {if !pairingAttempt {pairingAttempt=true;status="Conferma l’eventuale associazione richiesta da iOS";peripheral.readValue(for:characteristic)}else{status="Associazione Bluetooth non completata"};return}
        guard characteristic.isNotifying,!busy else{return};Task{await handshake()}
    }
    func peripheral(_ peripheral:CBPeripheral,didWriteValueFor characteristic:CBCharacteristic,error:Error?){if error != nil{failWaiter(AppError.message("Scrittura Bluetooth rifiutata"))}}
    func peripheral(_ peripheral:CBPeripheral,didUpdateValueFor characteristic:CBCharacteristic,error:Error?){
        if error != nil {failWaiter(AppError.message("Lettura Bluetooth rifiutata"));return}
        if pairingAttempt && !characteristic.isNotifying {peripheral.setNotifyValue(true,for:characteristic);return}
        guard let value=characteristic.value else{return}
        // Completa ogni frame prima di classificare i pacchetti sensibili.
        var decoder=framer
        defer{framer=decoder}
        do{_ = try decoder.consume(value){frame in
            if !OuraWire.sensitive(frame){
                var payload:[String:JSONValue]=["raw_hex":.string(frame.hex),"api_version":.array(self.apiVersion.map{.number(Double($0))})]
                if let logicalRing=self.logicalRing{payload["logical_ring_id"] = .string(logicalRing)}
                do{try self.store.append(kind:"sensor_packet",payload:.object(payload));self.onJournal?()}
                catch{self.desired=false;self.central.cancelPeripheralConnection(peripheral);throw AppError.message("Archivio non scrivibile: acquisizione fermata")}
            }
            self.receive(frame)
            self.maintenance()
        }}catch{
            if let raw=decoder.opaqueRejected {
                var payload:[String:JSONValue]=["raw_hex":.string(raw.hex),"api_version":.array(apiVersion.map{.number(Double($0))}),"transport_unparsed":.bool(true)]
                if let logicalRing{payload["logical_ring_id"] = .string(logicalRing)}
                do{try store.append(kind:"sensor_packet",payload:.object(payload));onJournal?()}catch{desired=false;central.cancelPeripheralConnection(peripheral)}
            }
            status=error.localizedDescription;failWaiter(error)
        }
    }
    private func receive(_ frame:Data){
        let b=[UInt8](frame)
        if b.count>=3,b[0]==0x2f,b[2]==0x28,b.count>=4,b[3]==2,ready,!apiVersion.isEmpty {
            if let live=try? OuraWire.live(frame,api:apiVersion){onLive?(live,Date())}
            return
        }
        if pageRecords != nil,let first=b.first,first>=0x41 {pageRecords?.append(frame);return}
        if b.count>=3,b[0]==0x2f,b[2]==0x2f {failWaiter(AppError.message("Autenticazione anello richiesta o rifiutata"));return}
        if b.count==3,b[0]==0x30 {failWaiter(AppError.message("Comando non supportato dal firmware"));return}
        if responseMatch?(frame)==true {let c=continuation;continuation=nil;responseMatch=nil;timeoutTask?.cancel();timeoutTask=nil;c?.resume(returning:frame)}
    }
    private func failWaiter(_ error:Error){let c=continuation;continuation=nil;responseMatch=nil;timeoutTask?.cancel();timeoutTask=nil;c?.resume(throwing:error)}
    private func exchange(_ request:Data,tag:UInt8,extended:UInt8?=nil,seconds:Double=15) async throws->Data {
        guard continuation==nil,let peripheral,peripheral.state == .connected,let characteristic=writeCharacteristic else{throw AppError.message("Anello occupato o scollegato")}
        return try await withCheckedThrowingContinuation{c in
            continuation=c;responseMatch={d in let b=[UInt8](d);return b.first==tag && (extended==nil || b.count>=3 && b[2]==extended!)}
            timeoutTask=Task{try? await Task.sleep(nanoseconds:UInt64(seconds*1_000_000_000));if !Task.isCancelled{failWaiter(AppError.message("Conferma anello non ricevuta"))}}
            peripheral.writeValue(request,for:characteristic,type:.withResponse)
        }
    }
    private func handshake() async {guard !busy else{return};busy=true;defer{busy=false;authenticating=false}
        do{
            status="Lettura firmware…";apiVersion=try OuraWire.firmware(await exchange(Data([8,3,0,0,0]),tag:9))
            var caps:[Int:Int]=[:];var totalPages:Int?
            for page in 0..<16 {
                let result=try OuraWire.capabilities(await exchange(Data([0x2f,2,1,UInt8(page)]),tag:0x2f,extended:2))
                if let totalPages,totalPages != result.0{throw AppError.message("Inventario capacità cambiato durante la lettura")}
                totalPages=result.0
                for(k,v)in result.1{if let old=caps[k],old != v{throw AppError.message("Versioni capacità incoerenti")};caps[k]=v}
                if page+1>=result.0{break}
            }
            capabilities=caps
            guard let logicalRing,let key=SecureStore.read("ring.\(logicalRing)"),key.count==16 else{throw AppError.message("Associa l’anello tramite FamilOS; nessun reset richiesto")}
            authenticating=true;status="Autenticazione del proprietario…"
            let nonceFrame=try OuraWire.complete(await exchange(Data([0x2f,1,0x2b]),tag:0x2f,extended:0x2c),minimum:18,tag:0x2f,extended:0x2c)
            guard nonceFrame.count==18 else{throw AppError.message("Nonce anello non valido")}
            let encrypted=try OuraWire.cipher(key:key,nonce:Data(nonceFrame[3..<18]))
            let answer=try OuraWire.complete(await exchange(Data([0x2f,17,0x2d])+encrypted,tag:0x2f,extended:0x2e),minimum:4,tag:0x2f,extended:0x2e)
            guard answer[3]==0 else{throw AppError.message("Credenziale anello rifiutata; nessun reset eseguito")}
            authenticating=false
            if let control=store.load(SportControl.self,key:sportKey),control.pendingStop {try await restoreSport(control)}
            guard caps[2] != nil else{throw AppError.message("Battito live non pubblicizzato dal firmware")}
            try OuraWire.ack(await exchange(Data([0x2f,3,0x22,2,3]),tag:0x2f,extended:0x23),extended:0x23,feature:2)
            try OuraWire.ack(await exchange(Data([0x2f,3,0x26,2,2]),tag:0x2f,extended:0x27),extended:0x27,feature:2)
            ready=true;status="Collegato · attendi un IBI valido"
            nextBatteryAt=Date().addingTimeInterval(60)
            do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(300)}catch{status="Lettura batteria rinviata"}
            let more=try await historyPages(maxPages:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:60)
        }catch{status=error.localizedDescription}
    }
    func downloadHistory()async{guard ready,!busy else{return};busy=true;defer{busy=false};do{let more=try await historyPages(maxPages:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:60)}catch{status=error.localizedDescription;nextHistoryAt=Date().addingTimeInterval(60)}}
    private func historyPages(maxPages:Int)async throws->Bool{
        let key="history.cursor.\(logicalRing ?? peripheral?.identifier.uuidString ?? "unknown")"
        var cursor=store.load(UInt32.self,key:key) ?? 0
        for _ in 0..<maxPages {
            pageRecords=[]
            let reply:Data
            do{reply=try await exchange(OuraWire.historyRequest(cursor:cursor),tag:0x11,seconds:25)}catch{pageRecords=nil;throw error}
            let records=pageRecords ?? [];pageRecords=nil
            let b=try OuraWire.complete(reply,minimum:3,tag:0x11)
            guard Int(b[2])==records.count,records.count<=64 else{throw AppError.message("Conteggio storico incoerente: cursore conservato")}
            var next=cursor,previous=cursor
            for record in records {let value=try OuraWire.cursor(record);guard value>=previous,value<UInt32.max else{throw AppError.message("Cursore storico inatteso")};previous=value;next=value+1}
            guard records.isEmpty || next>cursor else{throw AppError.message("Storico senza avanzamento")}
            try store.archiveHistory(frames:records,nextCursor:next,key:key,api:apiVersion,ring:logicalRing)
            for record in records{onHistory?(record)};onJournal?();cursor=next
            let bytesLeft=b.count>=8 ? UInt32(b[4])|(UInt32(b[5])<<8)|(UInt32(b[6])<<16)|(UInt32(b[7])<<24):0
            if bytesLeft==0 || records.isEmpty{status="Collegato · storico aggiornato";return false}
        }
        status="Collegato · recupero dello storico in corso";return true
    }
    func maintenance(){
        let now=Date()
        guard ready,!busy,now>=nextHistoryAt || now>=nextExerciseAt || now>=nextBatteryAt else{return}
        let historyDue=now>=nextHistoryAt,exerciseDue=now>=nextExerciseAt && capabilities[3] != nil,batteryDue=now>=nextBatteryAt
        nextExerciseAt=now.addingTimeInterval(60)
        if historyDue{nextHistoryAt=now.addingTimeInterval(60)}
        if batteryDue{nextBatteryAt=now.addingTimeInterval(60)}
        busy=true
        Task{defer{busy=false}
            if batteryDue{do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(300)}catch{status="Lettura batteria rinviata"}}
            if exerciseDue{do{try await exerciseRead()}catch{status="Lettura cardio attività rinviata"}}
            if historyDue{do{let more=try await historyPages(maxPages:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:60)}catch{status=error.localizedDescription}}
        }
    }
    func refreshBattery()async{
        guard ready,!busy else{return};busy=true;defer{busy=false}
        do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(300)}catch{status="Lettura batteria da riprovare";nextBatteryAt=Date().addingTimeInterval(10)}
    }
    private func batteryRead()async throws{
        let value=try OuraWire.battery(await exchange(Data([0x0c,0]),tag:0x0d,seconds:8))
        let at=Date();batteryLevel=value.level;batteryAt=at;onBattery?(value.level,at)
    }
    func readExercise()async{guard ready,!busy else{return};busy=true;defer{busy=false};do{try await exerciseRead();nextExerciseAt=Date().addingTimeInterval(60)}catch{status=error.localizedDescription}}
    private func exerciseRead()async throws{
        let state=try await exchange(Data([0x2f,2,0x20,3]),tag:0x2f,extended:0x21)
        let latest=try await exchange(Data([0x2f,2,0x24,3]),tag:0x2f,extended:0x25)
        let b=try OuraWire.complete(latest,minimum:5,tag:0x2f,extended:0x25)
        guard b[3]==3 else{throw AppError.message("Risposta cardio attività inattesa")}
        var value:[String:JSONValue]=["status_raw_hex":.string(state.hex),"latest_raw_hex":.string(latest.hex),"validated_live":.bool(false)]
        if b.count>=17,b[4]==0{value["heart_rate_value_raw"] = .number(Double(b[13]));value["quality_raw"] = .number(Double(b[9]))}
        exerciseRaw = .object(value)
    }
    private func restoreSport(_ control:SportControl)async throws{
        try OuraWire.ack(await exchange(Data([0x2f,3,0x22,3,0]),tag:0x2f,extended:0x23),extended:0x23,feature:3)
        if control.restoreMode==1{try OuraWire.ack(await exchange(Data([0x2f,3,0x22,3,1]),tag:0x2f,extended:0x23),extended:0x23,feature:3)}
        try store.save(Optional<SportControl>.none,key:sportKey)
    }
    func startSport(activity:UInt16,age:Int,resting:Int)async throws{
        guard ready,!busy,capabilities[3] != nil else{throw AppError.message("Anello non pronto per la registrazione cardio")};busy=true;defer{busy=false}
        let parameters=try OuraWire.sportParameters(activity:activity,age:age,resting:resting)
        let current=try OuraWire.complete(await exchange(Data([0x2f,2,0x20,3]),tag:0x2f,extended:0x21),minimum:8,tag:0x2f,extended:0x21)
        guard current[3]==3,current[7]==0,(0...1).contains(current[4]) else{throw AppError.message("Registrazione cardio già attiva o stato non modificabile")}
        try store.save(SportControl(restoreMode:current[4],pendingStop:true),key:sportKey)
        do{
            try OuraWire.ack(await exchange(Data([0x2f,3,0x22,3,0]),tag:0x2f,extended:0x23),extended:0x23,feature:3)
            try OuraWire.ack(await exchange(parameters,tag:0x2f,extended:0x2a),extended:0x2a,feature:3)
            try OuraWire.ack(await exchange(Data([0x2f,3,0x22,3,2]),tag:0x2f,extended:0x23),extended:0x23,feature:3)
            try store.save(SportControl(restoreMode:current[4],pendingStop:false),key:sportKey)
            status="Registrazione cardio attività richiesta"
        }catch{
            if let control=store.load(SportControl.self,key:sportKey){try? await restoreSport(control)}
            throw error
        }
    }
    func stopSport()async{
        guard var control=store.load(SportControl.self,key:sportKey) else{return}
        control.pendingStop=true
        do{try store.save(control,key:sportKey)}catch{status="Arresto cardio non salvato: riprova";return}
        guard ready else{return}
        if busy{Task{try? await Task.sleep(nanoseconds:1_000_000_000);await stopSport()};return}
        busy=true;defer{busy=false}
        do{try await restoreSport(control)}catch{status="Arresto cardio da confermare alla prossima connessione"}
    }
}
