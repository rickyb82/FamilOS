import Foundation
import CoreBluetooth
import Combine

@MainActor final class RingBridge:NSObject,ObservableObject,@preconcurrency CBCentralManagerDelegate,@preconcurrency CBPeripheralDelegate {
    @Published var status="Anello scollegato" {didSet{if status != oldValue{archiveStatus(status);writeBatteryDiagnostic()}}}
    @Published var devices:[CBPeripheral]=[]
    @Published var connected=false;@Published var ready=false;@Published var busy=false
    @Published var apiVersion:[Int]=[];@Published var capabilities:[Int:Int]=[:]
    @Published var exerciseRaw:JSONValue = .null
    @Published var exerciseStatus="Registrazione cardio dedicata non avviata"
    @Published var recordedWorkoutProbeActive=false
    @Published var recordedHistorySummary="Prova dai dati registrati non avviata"
    private var recordedCapture:RecordedWorkoutCapture?;private var recordedDrainUntil=Date.distantPast
    private var liveSubscriptionActive=false;private var lastHistoryBytesLeft:UInt32?;private var lastHistoryRecordCount:Int?
    private var exerciseStep="idle";private var exerciseAttemptAt:Date?;private var exerciseFailure:JSONValue?
    private var commandReplies:[JSONValue]=[];private var exerciseMode:OuraFeatureStatus?;private var exerciseModeAt:Date?
    private var setupIssues:[String]=[]
    private var modeWriteSkips:[JSONValue]=[]
    private var manualControlPending=false
    private var exerciseLatest:OuraExerciseLatest?;private var exerciseLatestAt:Date?;private var exerciseReadCount=0;private var exerciseHrChangeCount=0;private var previousExerciseHrRaw:Int?
    private var notifyCount=0;private var lastNotifyAt:Date?;private var nextNotifySnapshotAt=Date.distantPast;private var lastLiveProcessingDelay:TimeInterval?;private var delayedLivePacketCount=0
    private var displaySupportedLivePacketCount=0;private var lastDisplaySupportedLiveAt:Date?;private var ibiValidityCounts:[Int:Int]=[:]
    @Published var batteryLevel:Int?;@Published var batteryAt:Date?
    @Published var batteryStatus="Batteria non ancora letta"
    private let diagnosticWriter:DiagnosticWriter?;private var batteryAttemptAt:Date?;private var batteryReplyHex:String?;private var batteryChargingProgressRaw:Int?
    private var lastLivePacketAt:Date?;private var lastValidLivePacketAt:Date?
    private var livePacketCount=0;private var validLivePacketCount=0;private var stateFilteredValidIbiCount=0;private var flagFilteredValidIbiCount=0
    @Published var spo2Status="SpO₂: configurazione da verificare"
    private var spo2Before:OuraFeatureStatus?;private var spo2After:OuraFeatureStatus?
    @Published var lastConnectionError:String?
    var onBattery:((OuraBattery,Date)->Void)?
    var onLive:((OuraLive,Date)->Void)?;var onHistory:(()->Void)?;var onJournal:(()->Void)?
    private let statusJournalQueue=DispatchQueue(label:"HomePulse.status-journal",qos:.utility)
    private var central:CBCentralManager!;private var peripheral:CBPeripheral?
    private var writeCharacteristic:CBCharacteristic?;private var notifyCharacteristic:CBCharacteristic?
    private var connectionTimeout:Task<Void,Never>?;private var connectionFailure:String?
    private var reconnectTask:Task<Void,Never>?;private var reconnectGeneration=UUID();private var reconnectFailures=0;private var reconnectAt:Date?
    private var desired=false;private var authenticating=false;private var pairingAttempt=false
    private var foreground=true;private var chargeWakeDelay:TimeInterval?;private var waitingForChargeCheck=false
    private var logicalRing:String?;private var packetSession=UUID();private let packetJournal:PacketJournal
    private var continuation:CheckedContinuation<Data,Error>?
    private var responseMatch:((Data)->Bool)?;private var timeoutTask:Task<Void,Never>?
    private struct SportControl:Codable,Sendable{var restoreMode:UInt8;var pendingStop:Bool;var restoreLiveMode:UInt8?=nil;var restoreLiveSubscription:Bool?=nil}
    private var sportKey:String{"sport.control.\(logicalRing ?? "unbound")"}
    private var nextHistoryAt=Date.distantPast;private var nextExerciseAt=Date.distantPast;private var nextBatteryAt=Date.distantPast;private var nextSpO2At=Date.distantPast
    private var pageRecords:[Data]?;private let store:EventStore
    init(store:EventStore,diagnosticsURL:URL?=nil){self.store=store;self.packetJournal=PacketJournal(store:store);self.diagnosticWriter=diagnosticsURL.map{DiagnosticWriter(url:$0)};super.init();logicalRing=UserDefaults.standard.string(forKey:"ringLogicalID");desired=UserDefaults.standard.bool(forKey:"ringWantsConnection");central=CBCentralManager(delegate:self,queue:.main,options:[CBCentralManagerOptionRestoreIdentifierKey:"com.familos.homepulse.central",CBCentralManagerOptionShowPowerAlertKey:true])}
    func configure(logicalRing:String){self.logicalRing=logicalRing;UserDefaults.standard.set(logicalRing,forKey:"ringLogicalID")}
    private func archiveStatus(_ value:String){
        let store=store
        statusJournalQueue.async{[weak self] in
            _ = try? store.append(kind:"device_status",payload:.object(["source":.string("iphone_bluetooth"),"status":.string(value)]))
            Task{@MainActor [weak self] in self?.onJournal?()}
        }
    }
    var hasOwnerKey:Bool {guard let id=logicalRing else{return false};return SecureStore.read("ring.\(id)")?.count==16}
    var hasPendingConnection:Bool{desired && !ready}
    func scan(){guard central.state == .poweredOn else{status="Attiva il Bluetooth sull’iPhone";return};guard hasOwnerKey else{status="Attendi l’associazione dell’anello da FamilOS";return};devices=[];central.scanForPeripherals(withServices:[CBUUID(string:OuraWire.service)],options:nil);status="Ricerca anello…";Task{try? await Task.sleep(nanoseconds:15_000_000_000);central.stopScan()}}
    func connect(_ device:CBPeripheral,delay:TimeInterval=0,chargeCheck:Bool=false){
        guard hasOwnerKey else{status="Credenziale anello non ancora configurata";return}
        if !desired{desired=true;UserDefaults.standard.set(true,forKey:"ringWantsConnection")}
        peripheral=device;device.delegate=self
        guard device.state != .connecting else{status="Connessione…";return}
        if device.state == .disconnecting{desired=true;peripheral=device;scheduleReconnect(after:5);return}
        cancelReconnect()
        connectionTimeout?.cancel();connectionFailure=nil
        waitingForChargeCheck=chargeCheck
        central.stopScan()
        if UserDefaults.standard.string(forKey:"ringPeripheral") != device.identifier.uuidString{UserDefaults.standard.set(device.identifier.uuidString,forKey:"ringPeripheral")}
        var options:[String:Any]=[CBConnectPeripheralOptionNotifyOnDisconnectionKey:false]
        if delay>0{options[CBConnectPeripheralOptionStartDelayKey]=NSNumber(value:delay)}
        status=chargeCheck ? "Monitoraggio carica · prossimo controllo Bluetooth programmato":"Connessione…"
        if device.state == .connected{centralManager(central,didConnect:device);return}
        central.connect(device,options:options)
        writeBatteryDiagnostic()
        connectionTimeout=Task{try? await Task.sleep(nanoseconds:UInt64((delay+30)*1_000_000_000));guard !Task.isCancelled,peripheral===device,!connected else{return};connectionFailure="Collegamento non completato. Avvicina l’anello e riprova.";lastConnectionError=connectionFailure;status=connectionFailure!;central.cancelPeripheralConnection(device)}
    }
    func setForeground(_ active:Bool){
        foreground=active
        if active,waitingForChargeCheck,let peripheral{
            chargeWakeDelay=0
            if peripheral.state == .connecting{central.cancelPeripheralConnection(peripheral)}
        }else if !active{_ = scheduleChargingCheck()}
    }
    @discardableResult private func scheduleChargingCheck()->Bool{
        guard let delay=ChargeCheckPolicy.delay(foreground:foreground,desired:desired,ready:ready,busy:busy,progress:batteryChargingProgressRaw),let peripheral,peripheral.state == .connected else{return false}
        guard !waitingForChargeCheck else{return true}
        chargeWakeDelay=delay;waitingForChargeCheck=true
        status="Monitoraggio carica · preparo il prossimo controllo Bluetooth"
        writeBatteryDiagnostic();central.cancelPeripheralConnection(peripheral)
        return true
    }
    private func finishOperation(){busy=false;_ = scheduleChargingCheck();writeBatteryDiagnostic()}
    func disconnect(){cancelReconnect();reconnectFailures=0;connectionTimeout?.cancel();connectionFailure=nil;chargeWakeDelay=nil;waitingForChargeCheck=false;desired=false;UserDefaults.standard.set(false,forKey:"ringWantsConnection")
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
    func centralManager(_ central:CBCentralManager,didConnect peripheral:CBPeripheral){cancelReconnect();reconnectFailures=0;connectionTimeout?.cancel();connectionFailure=nil;lastConnectionError=nil;waitingForChargeCheck=false;connected=true;ready=false;pairingAttempt=false;packetSession=UUID();packetJournal.reset();peripheral.delegate=self;peripheral.discoverServices([CBUUID(string:OuraWire.service)]);status="Verifica servizi anello…"}
    func centralManager(_ central:CBCentralManager,didFailToConnect peripheral:CBPeripheral,error:Error?){connectionFailure=error.map{"Connessione non riuscita: \($0.localizedDescription)"};lastConnectionError=connectionFailure;lost()}
    func centralManager(_ central:CBCentralManager,didDisconnectPeripheral peripheral:CBPeripheral,error:Error?){lost()}
    private func lost(){
        let scheduledDelay=chargeWakeDelay;chargeWakeDelay=nil;packetSession=UUID()
        connectionTimeout?.cancel();connected=false;ready=false;writeCharacteristic=nil;notifyCharacteristic=nil;busy=false;authenticating=false
        failWaiter(AppError.message("Collegamento anello interrotto"));status=connectionFailure ?? "Anello scollegato"
        if desired,central.state == .poweredOn,let peripheral{
            if let scheduledDelay,connectionFailure==nil{
                // Planned charging checks retain their OS-owned delayed request.
                connect(peripheral,delay:scheduledDelay,chargeCheck:scheduledDelay>0)
            }else{
                // A rejected connect can callback immediately, before an OS
                // start-delay takes effect. Never resubmit inside that callback.
                reconnectFailures=min(5,reconnectFailures+1);waitingForChargeCheck=false
                scheduleReconnect(after:RingReconnectPolicy.delay(consecutiveFailures:reconnectFailures))
            }
        }else{waitingForChargeCheck=false}
    }
    private func cancelReconnect(){reconnectTask?.cancel();reconnectTask=nil;reconnectAt=nil;reconnectGeneration=UUID()}
    private func scheduleReconnect(after delay:TimeInterval){
        cancelReconnect();let generation=reconnectGeneration;reconnectAt=Date().addingTimeInterval(delay)
        reconnectTask=Task{[weak self] in
            try? await Task.sleep(nanoseconds:UInt64(delay*1_000_000_000))
            guard !Task.isCancelled,let self,self.reconnectGeneration==generation else{return}
            self.reconnectTask=nil;self.reconnectAt=nil
            guard self.desired,self.central.state == .poweredOn,let peripheral=self.peripheral else{return}
            self.connect(peripheral)
        }
        writeBatteryDiagnostic()
    }
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
        let receivedAt=Date();notifyCount+=1;lastNotifyAt=receivedAt
        if receivedAt>=nextNotifySnapshotAt{nextNotifySnapshotAt=receivedAt.addingTimeInterval(5);writeBatteryDiagnostic()}
        let session=packetSession
        packetJournal.ingest(Data(value),at:receivedAt,api:apiVersion,ring:logicalRing){[weak self] result in
            DispatchQueue.main.async{[weak self] in
                guard let self,self.packetSession==session else{return}
                if result.storageFailed{self.desired=false;self.packetSession=UUID();self.central.cancelPeripheralConnection(peripheral)}
                for frame in result.frames{self.receive(frame,at:result.at)}
                self.onJournal?()
                if let error=result.error{self.status=error;self.failWaiter(AppError.message(error))}
                self.maintenance()
            }
        }
    }
    private func receive(_ frame:Data,at receivedAt:Date){
        let b=[UInt8](frame)
        if recordedCapture != nil,b.first.map({$0>=0x41}) == true{recordedCapture?.note(frame,at:receivedAt)}
        if b.count>=5,b[0]==0x2f,Int(b[1])+2==b.count,[0x23,0x27,0x2a].contains(b[2]),[2,3,4].contains(b[3]){
            commandReplies.append(.object(["at":.number(receivedAt.timeIntervalSince1970),"step":.string(exerciseStep),"feature":.number(Double(b[3])),"reply":.number(Double(b[2])),"result_raw":.number(Double(b[4])),"frame_bytes":.number(Double(b.count)),"prefix_hex":.string(Data(frame.prefix(5)).hex)]))
            if commandReplies.count>16{commandReplies.removeFirst(commandReplies.count-16)}
            writeBatteryDiagnostic()
        }
        if b.count>=8,b[0]==0x2f,Int(b[1])+2==b.count,b[2]==0x21,b[3]==3{
            // Preserve unknown status values in technical evidence without promoting them to a measurement.
            exerciseMode=OuraFeatureStatus(mode:Int(b[4]),flags:Int(b[5]),state:Int(b[6]),result:Int(b[7]));exerciseModeAt=receivedAt;writeBatteryDiagnostic()
        }
        if b.first==0x0d,ready {
            batteryReplyHex=Data(frame.prefix(5)).hex
            do{let value=try OuraWire.battery(frame);let at=receivedAt;batteryLevel=value.level;batteryAt=at;batteryChargingProgressRaw=value.chargingProgressRaw;batteryStatus="Batteria ricevuta: \(value.level)%";onBattery?(value,at)}
            catch{batteryStatus=error.localizedDescription}
            writeBatteryDiagnostic()
        }
        if b.count>=3,b[0]==0x2f,b[2]==0x28,b.count>=4,b[3]==2,ready,!apiVersion.isEmpty {
            livePacketCount+=1;lastLivePacketAt=receivedAt
            let delay=max(0,Date().timeIntervalSince(receivedAt));lastLiveProcessingDelay=delay;if delay>2{delayedLivePacketCount+=1}
            if let live=try? OuraWire.live(frame,api:apiVersion){
                ibiValidityCounts[live.validity,default:0]+=1
                if b.count>=10{
                    let ibi=Int(b[8])|((Int(b[9])&15)<<8)
                    if live.validity==1,ibi>0,ibi<=2000{
                        if live.state != 2{stateFilteredValidIbiCount+=1}
                        else if live.flags&1==0{flagFilteredValidIbiCount+=1}
                    }
                }
                if let bpm=live.bpm{
                    validLivePacketCount+=1;lastValidLivePacketAt=receivedAt
                    if OuraWire.displaySupported(bpm){displaySupportedLivePacketCount+=1;lastDisplaySupportedLiveAt=receivedAt}
                };onLive?(live,receivedAt)
            }
            writeBatteryDiagnostic()
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
    private func handshake() async {guard !busy else{return};busy=true;setupIssues=[];exerciseStep="handshake";defer{authenticating=false;finishOperation()}
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
            // Authenticated transport remains usable even if an optional feature
            // rejects setup. Keep its error visible and its pending stop durable.
            authenticating=false;ready=true
            let sportKey=sportKey
            exerciseStep="handshake_restore"
            let control=try await store.perform({$0.load(SportControl.self,key:sportKey)})
            manualControlPending=control != nil
            recordedWorkoutProbeActive=control?.restoreLiveMode != nil && control?.pendingStop == false
            if recordedWorkoutProbeActive,recordedCapture==nil{
                recordedCapture=RecordedWorkoutCapture();recordedCapture?.phase="recording";recordedCapture?.countComplete=false
                recordedHistorySummary="Raccolta ripresa · riferimento iniziale da verificare"
            }
            if let control,control.pendingStop {
                do{try await restoreSport(control)}catch{exerciseStatus=error.localizedDescription;setupIssues.append(error.localizedDescription)}
            }
            exerciseStep="handshake_spo2"
            do{try await configureAutomaticSpO2()}catch{spo2Status="SpO₂: configurazione rinviata";nextSpO2At=Date().addingTimeInterval(60)}
            if caps[2] != nil{
                do{
                    exerciseStep="handshake_live_mode"
                    try await setFeatureMode(2,mode:recordedWorkoutProbeActive ? 1:3,operation:"Configurazione sorgente cardio")
                    exerciseStep="handshake_live_subscription"
                    try await setLiveSubscription(!recordedWorkoutProbeActive)
                }catch{setupIssues.append(error.localizedDescription)}
            }else{setupIssues.append("Battito live non pubblicizzato dal firmware")}
            exerciseStep="connected";status="Collegato · attendi un IBI valido"
            nextBatteryAt=Date().addingTimeInterval(60)
            do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(15)}catch{status="Lettura batteria rinviata"}
            let more=try await historyPages(maxPages:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:60)
            if !setupIssues.isEmpty{status="Collegato · "+setupIssues.joined(separator:"; ")}
        }catch{status=error.localizedDescription}
    }
    private func setFeatureMode(_ feature:UInt8,mode:UInt8,operation:String)async throws{
        guard capabilities[Int(feature)] != nil else{throw AppError.message(operation+": funzione non pubblicizzata dall’anello")}
        let current=try await exchange(Data([0x2f,2,0x20,feature]),tag:0x2f,extended:0x21)
        if try !OuraWire.modeChangeNeeded(current,feature:feature,desired:mode){
            modeWriteSkips.append(.object(["at":.number(Date().timeIntervalSince1970),"step":.string(exerciseStep),"feature":.number(Double(feature)),"mode":.number(Double(mode))]))
            if modeWriteSkips.count>8{modeWriteSkips.removeFirst(modeWriteSkips.count-8)}
            writeBatteryDiagnostic();return
        }
        try OuraWire.ack(await exchange(Data([0x2f,3,0x22,feature,mode]),tag:0x2f,extended:0x23),extended:0x23,feature:feature,operation:operation)
    }
    private func setLiveSubscription(_ enabled:Bool)async throws{
        try OuraWire.ack(await exchange(Data([0x2f,3,0x26,2,enabled ? 2:0]),tag:0x2f,extended:0x27),extended:0x27,feature:2,operation:"Configurazione ricezione cardio")
        liveSubscriptionActive=enabled
    }
    private func configureAutomaticSpO2()async throws{
        guard let version=capabilities[4] else{spo2Status="SpO₂: funzione non pubblicizzata dal firmware";nextSpO2At=Date.distantFuture;return}
        let before=try OuraWire.featureStatus(await exchange(Data([0x2f,2,0x20,4]),tag:0x2f,extended:0x21),feature:4)
        spo2Before=before;let key="spo2.parameters.\(logicalRing ?? "unbound")"
        if before.mode != 1{try OuraWire.ack(await exchange(Data([0x2f,3,0x22,4,1]),tag:0x2f,extended:0x23),extended:0x23,feature:4)}
        if let parameters=OuraWire.spo2AutomaticParameters(capabilityVersion:version){
            let desired=parameters.hex,cached=try await store.perform{$0.load(String.self,key:key)}
            if before.mode != 1 || cached != desired{
                try OuraWire.ack(await exchange(parameters,tag:0x2f,extended:0x2a),extended:0x2a,feature:4)
                try await store.perform{try $0.save(desired,key:key)}
            }
        }
        let after=try OuraWire.featureStatus(await exchange(Data([0x2f,2,0x20,4]),tag:0x2f,extended:0x21),feature:4)
        spo2After=after
        guard after.mode==1 else{throw AppError.message("Modalità SpO₂ automatica non confermata")}
        spo2Status=OuraWire.spo2AutomaticParameters(capabilityVersion:version) == nil ? "SpO₂ automatica attiva · parametri del firmware":"SpO₂ automatica attiva · ciclo richiesto 1 min · attesa iniziale 3 min"
        nextSpO2At=Date().addingTimeInterval(300);writeBatteryDiagnostic()
    }
    func refreshSpO2()async{guard ready,!busy else{return};busy=true;defer{finishOperation()};do{try await configureAutomaticSpO2()}catch{spo2Status="SpO₂: configurazione rinviata";nextSpO2At=Date().addingTimeInterval(60)}}
    func downloadHistory()async{guard ready,!busy else{return};busy=true;defer{finishOperation()};do{let more=try await historyPages(maxPages:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:60)}catch{status=error.localizedDescription;nextHistoryAt=Date().addingTimeInterval(60)}}
    private func historyPages(maxPages:Int)async throws->Bool{
        let key="history.cursor.\(logicalRing ?? peripheral?.identifier.uuidString ?? "unknown")"
        var cursor=try await store.perform{$0.load(UInt32.self,key:key) ?? 0}
        for _ in 0..<maxPages {
            let requestMax:UInt8=(recordedWorkoutProbeActive || recordedCapture?.phase=="baseline" || Date()<recordedDrainUntil) ? 255:64
            pageRecords=[]
            let reply:Data
            do{reply=try await exchange(OuraWire.historyRequest(cursor:cursor,max:requestMax),tag:0x11,seconds:25)}catch{pageRecords=nil;throw error}
            let records=pageRecords ?? [];pageRecords=nil
            let b=try OuraWire.complete(reply,minimum:3,tag:0x11)
            guard Int(b[2])==records.count,records.count<=Int(requestMax) else{throw AppError.message("Conteggio storico incoerente: cursore conservato")}
            var next=cursor,previous=cursor
            for record in records {let value=try OuraWire.cursor(record);guard value>=previous,value<UInt32.max else{throw AppError.message("Cursore storico inatteso")};previous=value;next=value+1}
            guard records.isEmpty || next>cursor else{throw AppError.message("Storico senza avanzamento")}
            let nextCursor=next,api=apiVersion,ring=logicalRing
            try await store.perform{try $0.archiveHistory(frames:records,nextCursor:nextCursor,key:key,api:api,ring:ring)}
            if !records.isEmpty{onHistory?()};onJournal?();cursor=next
            let bytesLeft=b.count>=8 ? UInt32(b[4])|(UInt32(b[5])<<8)|(UInt32(b[6])<<16)|(UInt32(b[7])<<24):0
            lastHistoryBytesLeft=b.count>=8 ? bytesLeft:nil
            lastHistoryRecordCount=records.count
            if recordedCapture != nil{
                recordedCapture?.pages+=1;recordedCapture?.lastPageAt=Date();recordedCapture?.bytesLeft=lastHistoryBytesLeft
                if let capture=recordedCapture{recordedHistorySummary="\(capture.recordingTraceCount) tracce cardio · \(capture.recordingAcmCount) record movimento acquisiti durante la prova"}
                writeBatteryDiagnostic()
            }
            if bytesLeft==0 || records.isEmpty{status="Collegato · storico aggiornato";return false}
        }
        status="Collegato · recupero dello storico in corso";return true
    }
    func maintenance(){
        let now=Date()
        if scheduleChargingCheck(){return}
        let historyDue=now>=nextHistoryAt,exerciseDue=now>=nextExerciseAt && capabilities[3] != nil,batteryDue=now>=nextBatteryAt,spo2Due=now>=nextSpO2At
        guard ready,!busy,historyDue || exerciseDue || batteryDue || spo2Due else{return}
        if spo2Due{nextSpO2At=now.addingTimeInterval(60)}
        nextExerciseAt=ExercisePolling.nextDeadline(previous:nextExerciseAt,now:now,due:exerciseDue,manualControlPending:manualControlPending)
        let historyInterval:TimeInterval=(manualControlPending || now<recordedDrainUntil) ? 5:60
        if historyDue{nextHistoryAt=now.addingTimeInterval(historyInterval)}
        if batteryDue{nextBatteryAt=now.addingTimeInterval(60)}
        busy=true
        Task{defer{finishOperation()}
            if spo2Due{do{try await configureAutomaticSpO2()}catch{spo2Status="SpO₂: configurazione rinviata"}}
            if batteryDue{do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(15)}catch{status="Lettura batteria rinviata"}}
            if exerciseDue{do{try await exerciseRead()}catch{status="Lettura cardio attività rinviata"}}
            if historyDue{do{let more=try await historyPages(maxPages:manualControlPending ? 2:32);nextHistoryAt=Date().addingTimeInterval(more ? 2:historyInterval)}catch{status=error.localizedDescription}}
        }
    }
    func refreshBattery()async{
        guard ready else{return};nextBatteryAt=Date.distantPast
        guard !busy else{return};busy=true;defer{finishOperation()}
        do{try await batteryRead();nextBatteryAt=Date().addingTimeInterval(15)}catch{status="Lettura batteria da riprovare";nextBatteryAt=Date().addingTimeInterval(10)}
    }
    private func batteryRead()async throws{
        batteryAttemptAt=Date();batteryStatus="Lettura batteria in corso…";writeBatteryDiagnostic()
        do{_ = try OuraWire.battery(await exchange(Data([0x0c,0]),tag:0x0d,seconds:8))}
        catch{batteryStatus=error.localizedDescription;writeBatteryDiagnostic();throw error}
    }
    private func writeBatteryDiagnostic(){
        guard let diagnosticWriter else{return}
        // Snapshot only; encoding and protected atomic file I/O run off-main.
        var value:[String:JSONValue]=["at":.number(Date().timeIntervalSince1970),"status":.string(batteryStatus),"ring_status":.string(status),"connected":.bool(connected),"ready":.bool(ready),"busy":.bool(busy),"foreground":.bool(foreground),"waiting_for_charge_check":.bool(waitingForChargeCheck),"bluetooth_state":.number(Double(central?.state.rawValue ?? -1)),"peripheral_state":.number(Double(peripheral?.state.rawValue ?? -1))]
        if let batteryAt{value["battery_read_at"] = .number(batteryAt.timeIntervalSince1970)}
        if let lastLivePacketAt{value["last_live_packet_at"] = .number(lastLivePacketAt.timeIntervalSince1970)}
        if let lastValidLivePacketAt{value["last_valid_live_packet_at"] = .number(lastValidLivePacketAt.timeIntervalSince1970)}
        if let spo2Before{value["spo2_mode_before"] = .number(Double(spo2Before.mode))}
        if let spo2After{value["spo2_mode"] = .number(Double(spo2After.mode));value["spo2_state_raw"] = .number(Double(spo2After.state));value["spo2_result_raw"] = .number(Double(spo2After.result))}
        if let batteryAttemptAt{value["attempt_at"] = .number(batteryAttemptAt.timeIntervalSince1970)}
        if let batteryReplyHex{value["battery_reply_hex"] = .string(batteryReplyHex)}
        if let batteryLevel{value["battery_percentage"] = .number(Double(batteryLevel))}
        if let batteryChargingProgressRaw{value["charging_progress_raw"] = .number(Double(batteryChargingProgressRaw))}
        value["reconnect_failures"] = .number(Double(reconnectFailures))
        value["live_packet_count"] = .number(Double(livePacketCount))
        value["valid_live_packet_count"] = .number(Double(validLivePacketCount))
        value["valid_ibi_filtered_by_state_count"] = .number(Double(stateFilteredValidIbiCount))
        value["valid_ibi_filtered_by_flag_count"] = .number(Double(flagFilteredValidIbiCount))
        value["display_supported_live_packet_count"] = .number(Double(displaySupportedLivePacketCount))
        value["live_ibi_validity_counts"] = .object(Dictionary(uniqueKeysWithValues:ibiValidityCounts.map{(String($0.key),.number(Double($0.value)))}))
        value["notify_count"] = .number(Double(notifyCount));value["delayed_live_packet_count"] = .number(Double(delayedLivePacketCount))
        if let lastNotifyAt{value["last_notify_at"] = .number(lastNotifyAt.timeIntervalSince1970)}
        if let lastLiveProcessingDelay{value["last_live_processing_delay_ms"] = .number(lastLiveProcessingDelay*1000)}
        if let lastDisplaySupportedLiveAt{value["last_display_supported_live_at"] = .number(lastDisplaySupportedLiveAt.timeIntervalSince1970)}
        value["manual_exercise_control_pending"] = .bool(manualControlPending)
        value["recorded_workout_probe_active"] = .bool(recordedWorkoutProbeActive)
        value["live_subscription_active"] = .bool(liveSubscriptionActive)
        if let recordedCapture{value["recorded_workout_capture"] = recordedCapture.diagnostic}
        value["exercise_latest_read_count"] = .number(Double(exerciseReadCount));value["exercise_latest_hr_change_count"] = .number(Double(exerciseHrChangeCount))
        if let exerciseLatestAt{value["exercise_latest_read_at"] = .number(exerciseLatestAt.timeIntervalSince1970)}
        if let exerciseLatest{
            value["exercise_latest_result_raw"] = .number(Double(exerciseLatest.result))
            if let flags=exerciseLatest.flags{value["exercise_latest_flags_raw"] = .number(Double(flags))}
            if let state=exerciseLatest.state{value["exercise_latest_state_raw"] = .number(Double(state))}
            if let age=exerciseLatest.ageRaw{value["exercise_latest_age_raw"] = .number(Double(age))}
            if let quality=exerciseLatest.qualityRaw{value["exercise_latest_quality_raw"] = .number(Double(quality))}
            value["exercise_latest_last_hr_nonzero"] = .bool(exerciseLatest.result==0 && (exerciseLatest.lastHrRaw ?? 0)>0)
        }
        value["exercise_step"] = .string(exerciseStep)
        value["command_replies"] = .array(commandReplies)
        value["setup_issues"] = .array(setupIssues.map{.string($0)})
        value["mode_write_skips"] = .array(modeWriteSkips)
        if let exerciseAttemptAt{value["exercise_attempt_at"] = .number(exerciseAttemptAt.timeIntervalSince1970)}
        if let exerciseFailure{value["exercise_failure"] = exerciseFailure}
        if let exerciseMode{
            value["exercise_mode"] = .number(Double(exerciseMode.mode));value["exercise_state_raw"] = .number(Double(exerciseMode.state));value["exercise_status_result_raw"] = .number(Double(exerciseMode.result))
        }
        if let exerciseModeAt{value["exercise_status_at"] = .number(exerciseModeAt.timeIntervalSince1970)}
        if let reconnectAt{value["reconnect_retry_at"] = .number(reconnectAt.timeIntervalSince1970)}
        diagnosticWriter.submit(.object(value))
    }
    func readExercise()async{guard ready,!busy else{return};busy=true;defer{finishOperation()};do{try await exerciseRead();nextExerciseAt=ExercisePolling.nextDeadline(previous:nextExerciseAt,now:Date(),due:true,manualControlPending:manualControlPending)}catch{status=error.localizedDescription}}
    private func exerciseRead()async throws{
        let state=try await exchange(Data([0x2f,2,0x20,3]),tag:0x2f,extended:0x21)
        let latest=try await exchange(Data([0x2f,2,0x24,3]),tag:0x2f,extended:0x25)
        let parsed=try OuraWire.exerciseLatest(latest)
        var value:[String:JSONValue]=["status_raw_hex":.string(state.hex),"latest_raw_hex":.string(latest.hex),"validated_live":.bool(false)]
        if parsed.result==0,let hr=parsed.lastHrRaw,let quality=parsed.qualityRaw{
            value["heart_rate_value_raw"] = .number(Double(hr));value["quality_raw"] = .number(Double(quality))
            if let previousExerciseHrRaw,previousExerciseHrRaw != hr{exerciseHrChangeCount+=1};previousExerciseHrRaw=hr
        }
        exerciseLatest=parsed;exerciseLatestAt=Date();exerciseReadCount+=1
        exerciseRaw = .object(value)
        if let exerciseMode{
            let next:String
            switch exerciseMode.mode{
            case 0:next="Registrazione cardio dedicata non attiva"
            case 1:next="Cardio attività in modalità automatica"
            case 2:next="Registrazione cardio dedicata richiesta"
            default:next="Stato cardio attività da verificare"
            }
            if exerciseStatus != next{exerciseStatus=next}
        }
        writeBatteryDiagnostic()
    }
    private func restoreSport(_ control:SportControl)async throws{
        let callerStep=exerciseStep;exerciseStep=callerStep+"_off"
        try await setFeatureMode(3,mode:0,operation:"Ripristino cardio attività")
        if control.restoreMode==1{
            exerciseStep=callerStep+"_auto"
            try await setFeatureMode(3,mode:1,operation:"Ripristino cardio automatico")
        }
        if let mode=control.restoreLiveMode{
            exerciseStep=callerStep+"_live_restore";try await setFeatureMode(2,mode:mode,operation:"Ripristino sorgente cardio")
            try await setLiveSubscription(control.restoreLiveSubscription ?? false)
        }
        let key=sportKey
        try await store.perform{try $0.save(Optional<SportControl>.none,key:key)}
        manualControlPending=false
        if recordedWorkoutProbeActive{
            recordedWorkoutProbeActive=false;recordedCapture?.phase="after_stop";recordedCapture?.stoppedAt=Date();recordedDrainUntil=Date().addingTimeInterval(60);nextHistoryAt=Date.distantPast
        }
        exerciseStep=callerStep
    }
    func startSport(activity:UInt16,age:Int,resting:Int,recordedOnly:Bool=false)async throws{
        guard ready,!busy,capabilities[3] != nil else{throw AppError.message("Anello non pronto per la registrazione cardio")};busy=true;defer{finishOperation()}
        let parameters=try OuraWire.sportParameters(activity:activity,age:age,resting:resting,capabilityVersion:capabilities[3] ?? 0)
        exerciseAttemptAt=Date();exerciseFailure=nil;exerciseStep="query_status";exerciseStatus="Verifica della registrazione cardio…";writeBatteryDiagnostic()
        let pendingKey=sportKey
        if let pending=try await store.perform({$0.load(SportControl.self,key:pendingKey)}),pending.pendingStop{
            exerciseStep="start_pending_restore";try await restoreSport(pending)
        }
        let current=try OuraWire.complete(await exchange(Data([0x2f,2,0x20,3]),tag:0x2f,extended:0x21),minimum:8,tag:0x2f,extended:0x21)
        guard current[3]==3,(0...1).contains(current[4]) else{throw AppError.message("Registrazione cardio già attiva o stato non modificabile")}
        var liveMode:UInt8?
        if !recordedOnly{recordedCapture=nil;recordedDrainUntil=Date.distantPast}
        if recordedOnly{
            let live=try OuraWire.complete(await exchange(Data([0x2f,2,0x20,2]),tag:0x2f,extended:0x21),minimum:8,tag:0x2f,extended:0x21)
            guard live[3]==2,[0,1,3].contains(live[4]) else{throw AppError.message("Sorgente cardio già occupata da un’altra misura")}
            liveMode=live[4];recordedCapture=RecordedWorkoutCapture();recordedHistorySummary="Sincronizzazione iniziale della prova…"
            let more=try await historyPages(maxPages:8)
            recordedCapture?.baselineComplete = !more && (lastHistoryBytesLeft==0 || (lastHistoryBytesLeft==nil && lastHistoryRecordCount==0))
            let cursorKey="history.cursor.\(logicalRing ?? peripheral?.identifier.uuidString ?? "unknown")"
            recordedCapture?.baselineNextRingTime=try await store.perform{$0.load(UInt32.self,key:cursorKey)}
            guard recordedCapture?.baselineComplete == true else{nextHistoryAt=Date.distantPast;throw AppError.message("Recupero dei dati precedenti in corso: riprova quando lo storico è allineato")}
        }
        let key=sportKey
        let control=SportControl(restoreMode:current[4],pendingStop:true,restoreLiveMode:liveMode,restoreLiveSubscription:recordedOnly ? liveSubscriptionActive:nil)
        try await store.perform{try $0.save(control,key:key)}
        manualControlPending=true
        do{
            if recordedOnly{
                exerciseStep="recorded_only_source";recordedWorkoutProbeActive=true
                try await setFeatureMode(2,mode:1,operation:"Preparazione cardio registrato");try await setLiveSubscription(false)
            }
            exerciseStep="off";exerciseStatus="Preparazione cardio attività…";writeBatteryDiagnostic()
            try await setFeatureMode(3,mode:0,operation:"Preparazione cardio attività")
            exerciseStep="parameters";exerciseStatus="Configurazione cardio attività…";writeBatteryDiagnostic()
            try OuraWire.ack(await exchange(parameters,tag:0x2f,extended:0x2a),extended:0x2a,feature:3,operation:"Parametri cardio attività")
            exerciseStep="requested";exerciseStatus="Avvio cardio attività…";writeBatteryDiagnostic()
            try await setFeatureMode(3,mode:2,operation:"Avvio cardio attività")
            var active=control;active.pendingStop=false;let activeControl=active
            try await store.perform{try $0.save(activeControl,key:key)}
            if recordedOnly{recordedCapture?.phase="recording";recordedCapture?.requestedAt=Date();recordedHistorySummary="Raccolta progressiva dei dati dell’allenamento…"}
            exerciseStep="accepted";exerciseStatus="Richiesta di avvio accettata dall’anello";status=exerciseStatus
            nextExerciseAt=Date.distantPast
            nextHistoryAt=Date.distantPast
        }catch{
            var failure:[String:JSONValue]=["at":.number(Date().timeIntervalSince1970),"step":.string(exerciseStep)]
            if let rejection=error as? OuraCommandRejection{failure["feature"] = .number(Double(rejection.feature));failure["reply"] = .number(Double(rejection.command));failure["result_raw"] = .number(Double(rejection.result))}
            exerciseFailure = .object(failure);exerciseStatus=error.localizedDescription;exerciseStep="restore_after_failure";writeBatteryDiagnostic()
            if let control=try? await store.perform({$0.load(SportControl.self,key:key)}){try? await restoreSport(control)}
            exerciseStep="failed";writeBatteryDiagnostic()
            throw error
        }
    }
    func stopSport()async{
        let key=sportKey
        guard var control=try? await store.perform({$0.load(SportControl.self,key:key)}) else{return}
        control.pendingStop=true
        let pendingControl=control
        do{try await store.perform{try $0.save(pendingControl,key:key)}}catch{status="Arresto cardio non salvato: riprova";return}
        manualControlPending=true
        guard ready else{return}
        if busy{Task{try? await Task.sleep(nanoseconds:1_000_000_000);await stopSport()};return}
        busy=true;defer{finishOperation()}
        if recordedWorkoutProbeActive{recordedCapture?.phase="after_stop";recordedCapture?.stopRequestedAt=Date()}
        exerciseStep="stop";exerciseStatus="Arresto registrazione cardio…"
        do{try await restoreSport(control);exerciseStep="stopped";exerciseStatus="Richiesta di arresto cardio accettata";nextExerciseAt=Date.distantPast}
        catch{exerciseStatus=error.localizedDescription;status="Arresto cardio da confermare alla prossima connessione"}
    }
}
