import Foundation
import Combine
import UserNotifications

/// Loaded before constructing the UI. Database migrations and archive reads
/// never run on the actor responsible for drawing and handling taps.
struct AppArchiveSnapshot:Sendable {
    var settings:LocalSettings;var current:WorkoutSession?;var sessions:[WorkoutSession];var readings:[Reading]
    var metrics:HistoricalMetrics;var bootstrap:JSONValue;var analysis:JSONValue;var sleep:JSONValue
    var owner:String?;var logicalRing:String?;var battery:RingBatterySnapshot?
    var batteryAlert:BatteryAlertState;var fullAlert:BatteryFullAlertState
    var pending:Int;var journalCount:Int;var continuousCardio:Bool;var detection:DetectionState;var coordinates:[String:[Double]]
    static func load(_ store:EventStore)->Self{
        let settings=store.load(LocalSettings.self,key:"settings") ?? LocalSettings()
        let active=store.load(WorkoutSession.self,key:"activeWorkout").flatMap{store.diaryAllows($0) ? $0:nil}
        let coordinate=store.load([Double].self,key:"gymCoordinate.\(settings.auto_detection.gym.id)")
        return Self(settings:settings,current:active.flatMap{["running","paused"].contains($0.state) ? $0:nil},sessions:store.sessions(),readings:[],metrics:store.metrics(),bootstrap:.null,analysis:.null,sleep:.null,owner:store.load(String.self,key:"ownerName"),logicalRing:store.load(String.self,key:"logicalRing"),battery:store.load(RingBatterySnapshot.self,key:"ringBattery.latest"),batteryAlert:store.load(BatteryAlertState.self,key:"ringBattery.alert") ?? BatteryAlertState(),fullAlert:store.load(BatteryFullAlertState.self,key:"ringBattery.fullAlert") ?? BatteryFullAlertState(),pending:store.pendingCount,journalCount:store.journalCount,continuousCardio:store.load(Bool.self,key:"cardio.continuous.enabled") ?? true,detection:store.load(DetectionState.self,key:"gymDetection") ?? DetectionState(),coordinates:coordinate.map{[settings.auto_detection.gym.id:$0]} ?? [:])
    }
}

@MainActor final class TransferProgress:ObservableObject {
    @Published var pending=0;@Published var journalCount=0;@Published var status="Invio in attesa"
}
@MainActor final class AppModel:ObservableObject {
    @Published var settings:LocalSettings
    @Published var current:WorkoutSession?
    @Published var sessions:[WorkoutSession]=[];@Published var readings:[Reading]=[]
    var now:Date{Date()};var heartDisplay=HeartDisplayState()
    @Published private var heartBPM:Int?
    private var normalAverage=FiveReadingAverage()
    @Published private(set) var continuousCardio=false
    @Published private(set) var continuousStatus="Cardio esteso disattivato"
    private var pendingSportStart:Bool?;private var pendingSportUntil=Date.distantPast
    private var controllingContinuous=false;private var nextContinuousAttempt=Date.distantPast
    private var readingBuffer:[Reading]=[];private var lastChartUpdate=Date.distantPast
    var bpm:Int?{heartDisplay.bpm};var sampleAt:Date?{heartDisplay.capturedAt}
    @Published var warnings:[String]=[];@Published var temperatureRaw:Int?
    @Published var metrics=HistoricalMetrics();@Published var serverData:JSONValue = .null
    @Published var analysisData:JSONValue = .null
    @Published private var chartCache:[String:[ChartPoint]]=[:];private var chartRevision=0
    @Published var sleepData:JSONValue = .null;@Published var sleepStatus="";@Published var refreshingSleep=false
    let transfer=TransferProgress()
    var uploadStatus:String{get{transfer.status}set{if transfer.status != newValue{transfer.status=newValue}}}
    var pending:Int{get{transfer.pending}set{if transfer.pending != newValue{transfer.pending=newValue}}}
    var journalCount:Int{get{transfer.journalCount}set{if transfer.journalCount != newValue{transfer.journalCount=newValue}}}
    @Published var syncStatus="Offline pronto";@Published var message:String?
    @Published var ownerName:String?;@Published var enrolled=false
    @Published private(set) var changingWorkout=false
    #if DEBUG
    @Published var archiveTestStall=false
    @Published var diagnosticTestStall=false
    #endif
    private var workoutRevision=0;private var persistedSettings:LocalSettings
    @Published var allowedActivities:[(Int,String)]=ActivityCatalog.defaults
    @Published var batteryLevel:Int?;@Published var batteryAt:Date?
    @Published var batteryChargingProgressRaw:Int?
    var batteryChargeState:RingChargeState?{RingChargeState(progress:batteryChargingProgressRaw)}
    @Published var batteryNotificationStatus="Notifiche da autorizzare"
    private var batteryAlert=BatteryAlertState();private var batteryNotificationPending=false
    private var batteryFullAlert=BatteryFullAlertState();private var batteryFullNotificationPending=false
    let store:EventStore;let api:WellnessAPI;let ring:RingBridge;let gym:GymDetection
    let recorded:RecordedCardioController
    private var ticking:Task<Void,Never>?;private var refreshing=false
    private var foreground=false;private var sleepRefreshSchedule=SleepRefreshSchedule()
    private var nextStorageMaintenance=Date.distantPast
    private var refreshingArchive=false;private var archiveNeedsMetrics=false;private var lastArchiveRefresh=Date.distantPast
    private let notificationDiagnostics:DiagnosticWriter
    private let storageDiagnostics:DiagnosticWriter
    private let diaryDiagnostics:DiagnosticWriter
    private let sleepDiagnostics:DiagnosticWriter
    init(store:EventStore,folder:URL,snapshot:AppArchiveSnapshot){
        storageDiagnostics=DiagnosticWriter(url:folder.appendingPathComponent("storage-status.json"))
        diaryDiagnostics=DiagnosticWriter(url:folder.appendingPathComponent("diary-status.json"))
        sleepDiagnostics=DiagnosticWriter(url:folder.appendingPathComponent("sleep-status.json"))
        self.store=store;notificationDiagnostics=DiagnosticWriter(url:folder.appendingPathComponent("notification-status.json"))
        settings=snapshot.settings;persistedSettings=snapshot.settings;continuousCardio=snapshot.continuousCardio
        api=WellnessAPI(store:store,folder:folder.appendingPathComponent("uploads"))
        ring=RingBridge(store:store,diagnosticsURL:folder.appendingPathComponent("battery-status.json"));gym=GymDetection(store:store,state:snapshot.detection,coordinates:snapshot.coordinates)
        recorded=RecordedCardioController(store:store,diagnosticsURL:folder.appendingPathComponent("recorded-model-status.json"))
        recorded.continuousMode=snapshot.continuousCardio
        enrolled=SecureStore.credentials() != nil
        current=snapshot.current;sessions=snapshot.sessions;readingBuffer=snapshot.readings;readings=Self.displayReadings(snapshot.readings,average:snapshot.current==nil);metrics=snapshot.metrics
        serverData=snapshot.bootstrap;analysisData=snapshot.analysis;sleepData=snapshot.sleep;ownerName=snapshot.owner
        store.startRetentionMaintenance(diagnostic:storageDiagnostics)
        refreshChartCache()
        if let logical=snapshot.logicalRing{ring.configure(logicalRing:logical)}
        gym.apply(settings.auto_detection)
        api.onStatus={[weak self] status in Task{@MainActor in self?.uploadStatus=status}}
        api.onUpload={[weak self] in Task{@MainActor in self?.refreshArchiveStatus()}}
        api.onAcknowledged={[weak self] in Task{@MainActor in self?.refreshArchiveStatus();self?.maintainStorage();self?.refreshSleepAutomatically()}}
        ring.onBluetoothActivity={[weak self] in self?.api.noteBluetoothActivity()}
        ring.onLive={[weak self] value,time in self?.receive(value,at:time)}
        ring.onHistory={[weak self] in self?.archiveNeedsMetrics=true;self?.recorded.historyChanged()}
        ring.onRecordedPrepare={[weak self] activity in
            guard let self,let id=ring.logicalRingID else{throw AppError.message("Configurazione cardio incompleta")}
            try await recorded.prepare(ring:id,version:ring.capabilities[3] ?? 0,activity:activity,profile:settings.profile,workoutID:continuousCardio ? nil:current.map{$0.remoteID ?? $0.id.uuidString})
        }
        ring.onRecordedStartResolved={[weak self] accepted in try await self?.recorded.resolvePreparedStart(accepted)}
        recorded.onResult={[weak self] value in self?.receiveRecorded(value)}
        ring.onIdle={[weak self] in self?.maintainContinuousCardio()}
        ring.onJournal={[weak self] in self?.api.upload()}
        gym.onCandidate={[weak self] id,date in self?.startWorkout(auto:true,id:id,at:date)}
        gym.onReply={[weak self] id,name,negative in self?.resolveCandidate(id:id,name:name,negative:negative)}
        gym.onRegion={[weak self] inside in self?.regionEvent(inside)}
        if let battery=snapshot.battery{batteryLevel=battery.level;batteryAt=battery.at;batteryChargingProgressRaw=battery.chargingProgressRaw}
        batteryAlert=snapshot.batteryAlert;batteryFullAlert=snapshot.fullAlert
        ring.onBattery={[weak self] value,date in self?.receiveBattery(value.level,at:date,chargingProgressRaw:value.chargingProgressRaw)}
        Task{await refreshBatteryNotificationStatus()}
        pending=snapshot.pending;journalCount=snapshot.journalCount
        if current==nil,let id=gym.engine.state.candidateSessionID,let date=gym.engine.state.candidateAt{startWorkout(auto:true,id:id,at:date)}
        ticking=Task{[weak self] in var ticks=0;while !Task.isCancelled{try? await Task.sleep(nanoseconds:1_000_000_000);guard !Task.isCancelled,let self else{return};ring.maintenance();recorded.tick();maintainContinuousCardio();refreshArchiveStatus();refreshSleepAutomatically();ticks+=1;if ticks%30==0{Task{await self.refresh()}}}}
        let arguments=ProcessInfo.processInfo.arguments
        if let i=arguments.firstIndex(of:"--diary-keep-window"),i+3<arguments.count,let start=Double(arguments[i+1]),let end=Double(arguments[i+2]),let cutoff=Double(arguments[i+3]){
            let rule=DiaryCleanup(retain_start:start,retain_end:end,created_before:cutoff)
            if rule.valid{Task{do{try await applyDiaryCleanup(rule,pending:true);await refresh()}catch{message=error.localizedDescription}}}
        }

        if let i=arguments.firstIndex(of:"--energy-sex"),i+1<arguments.count,["male","female"].contains(arguments[i+1]){let sex=arguments[i+1];Task{await setEnergySex(sex)}}
        if ProcessInfo.processInfo.arguments.contains("--enable-continuous-cardio"){Task{await setContinuousCardio(true)}}
        if ProcessInfo.processInfo.arguments.contains("--spo2-stop-probe") {
            Task{[weak self] in for _ in 0..<90 {
                guard let self else{return}
                if ring.ready && !ring.busy{await ring.stopSpO2Probe();return}
                try? await Task.sleep(nanoseconds:1_000_000_000)
            }}
        }else if ProcessInfo.processInfo.arguments.contains("--spo2-requested-probe") {
            Task{[weak self] in
                for _ in 0..<90 {
                    guard let self else{return}
                    if ring.ready && !ring.busy{do{try await ring.startSpO2Probe()}catch{message=error.localizedDescription};return}
                    try? await Task.sleep(nanoseconds:1_000_000_000)
                }
            }
        }
    }
    func setForeground(_ active:Bool){
        let becameActive=active && !foreground;foreground=active
        ring.setForeground(active);api.setForeground(active)
        if becameActive{sleepRefreshSchedule.becameActive(at:Date());refreshSleepAutomatically()}
    }
    private func refreshSleepAutomatically(){
        guard sleepRefreshSchedule.reserve(at:Date(),foreground:foreground,enrolled:enrolled,inFlight:refreshingSleep) else{return}
        Task{[weak self] in await self?.refreshSleep()}
    }
    private func maintainStorage(){
        guard Date()>=nextStorageMaintenance else{return};nextStorageMaintenance=Date().addingTimeInterval(10)
        store.startRetentionMaintenance(diagnostic:storageDiagnostics)
    }
    private func refreshArchiveStatus(){
        guard !refreshingArchive,Date().timeIntervalSince(lastArchiveRefresh)>=1 else{return}
        refreshingArchive=true;lastArchiveRefresh=Date()
        let includeMetrics=archiveNeedsMetrics;archiveNeedsMetrics=false;let store=store
        Task{[weak self] in
            let value=try? await store.perform{( $0.pendingCount,$0.journalCount,includeMetrics ? $0.metrics():nil,$0.sessions() )}
            guard let self else{return}
            if let value{pending=value.0;journalCount=value.1;if let historical=value.2{metrics=historical};sessions=value.3};refreshingArchive=false
        }
    }
    func refreshBatteryNotificationStatus()async{
        let permission=await UNUserNotificationCenter.current().notificationSettings()
        batteryNotificationStatus=permission.authorizationStatus == .authorized || permission.authorizationStatus == .provisional ? "Avvisi al 10% e a carica completa attivi":"Notifiche da autorizzare nelle impostazioni iPhone"
    }
    func requestBatteryNotifications()async{
        do{_ = try await UNUserNotificationCenter.current().requestAuthorization(options:[.alert,.sound]);await refreshBatteryNotificationStatus()
            if let level=batteryLevel,let date=batteryAt,Date().timeIntervalSince(date)<600{receiveBattery(level,at:date,chargingProgressRaw:batteryChargingProgressRaw)}
        }catch{message="Autorizzazione notifiche non completata"}
    }
    private func receiveBattery(_ level:Int,at date:Date,chargingProgressRaw:Int?){
        guard (0...100).contains(level) else{return}
        batteryLevel=level;batteryAt=date;batteryChargingProgressRaw=chargingProgressRaw
        let snapshot=RingBatterySnapshot(level:level,at:date,chargingProgressRaw:chargingProgressRaw)
        store.enqueue({try $0.save(snapshot,key:"ringBattery.latest")},onError:{[weak self] _ in Task{@MainActor in self?.message="Livello batteria non salvato"}})
        notifyFullCharge(level:level,progress:chargingProgressRaw)
        if level>=20{_ = batteryAlert.observe(level);let alert=batteryAlert;store.enqueue{try $0.save(alert,key:"ringBattery.alert")}}
        guard level<=10,batteryAlert.armed,!batteryNotificationPending else{return}
        batteryNotificationPending=true
        Task{defer{batteryNotificationPending=false}
            let center=UNUserNotificationCenter.current(),permission=await UNUserNotificationCenter.current().notificationSettings()
            guard permission.authorizationStatus == .authorized || permission.authorizationStatus == .provisional else{await refreshBatteryNotificationStatus();return}
            // A later charging sample must not produce an alert for an obsolete low reading.
            guard let latest=batteryLevel,latest<=10,batteryAlert.armed else{return}
            let content=UNMutableNotificationContent();content.title="Batteria Oura al \(latest)%"
            content.body="Metti l’anello in carica per continuare a registrare gli allenamenti.";content.sound = .default
            do{try await center.add(UNNotificationRequest(identifier:"homepulse.ring-battery-low",content:content,trigger:nil))
                _ = batteryAlert.observe(latest);let alert=batteryAlert;try await store.perform{try $0.save(alert,key:"ringBattery.alert")}
            }catch{message="Avviso batteria non completato"}
        }
    }
    private func notifyFullCharge(level:Int,progress:Int?){
        guard RingChargeState(progress:progress) == .full else{
            _ = batteryFullAlert.observe(level:level,progress:progress)
            let alert=batteryFullAlert;store.enqueue{try $0.save(alert,key:"ringBattery.fullAlert")}
            return
        }
        guard batteryFullAlert.armed,!batteryFullNotificationPending else{return}
        batteryFullNotificationPending=true
        Task{defer{batteryFullNotificationPending=false}
            let execution=ShortBackgroundExecution(name:"HomePulse full charge notification");defer{execution.finish()}
            let center=UNUserNotificationCenter.current(),permission=await center.notificationSettings()
            writeNotificationDiagnostic("checking_permission",permission:permission.authorizationStatus.rawValue)
            guard permission.authorizationStatus == .authorized || permission.authorizationStatus == .provisional else{writeNotificationDiagnostic("not_authorized",permission:permission.authorizationStatus.rawValue);await refreshBatteryNotificationStatus();return}
            guard batteryChargeState == .full,batteryFullAlert.armed,let latest=batteryLevel else{return}
            let content=UNMutableNotificationContent();content.title="Oura completamente carico"
            content.body="L’anello ha completato la carica. Puoi toglierlo dal caricatore e indossarlo.";content.sound = .default
            do{try await center.add(UNNotificationRequest(identifier:"homepulse.ring-battery-full",content:content,trigger:nil))
                _ = batteryFullAlert.observe(level:latest,progress:100)
                let alert=batteryFullAlert;try await store.perform{try $0.save(alert,key:"ringBattery.fullAlert")}
                writeNotificationDiagnostic("full_charge_notification_accepted",permission:permission.authorizationStatus.rawValue)
            }catch{writeNotificationDiagnostic("full_charge_notification_failed");message="Avviso di carica completa non completato"}
        }
    }
    private func writeNotificationDiagnostic(_ status:String,permission:Int?=nil){
        var value:[String:JSONValue]=["at":.number(Date().timeIntervalSince1970),"status":.string(status),"full_alert_armed":.bool(batteryFullAlert.armed)]
        if let permission{value["authorization_status"] = .number(Double(permission))}
        notificationDiagnostics.submit(.object(value))
    }
    var displayedBPM:Int?{heartBPM}
    var hasFreshBPM:Bool{ring.ready && heartDisplay.isFresh(at:now)}
    var heartSampleAge:Int?{heartDisplay.ageSeconds(at:now)}
    private func receive(_ value:OuraLive,at date:Date){
        if warnings != value.warnings{warnings=value.warnings};if temperatureRaw != value.temperatureRaw{temperatureRaw=value.temperatureRaw}
        guard heartDisplay.receive(value,at:date),let hr=heartDisplay.bpm else{
            if let hr=value.bpm,!OuraWire.displaySupported(hr){warnings.append("Battito fuori intervallo di visualizzazione: pacchetto conservato")}
            return
        }
        let point=Reading(date:date,bpm:hr)
        let averaged=normalAverage.accept(point)
        if let displayed=current == nil ? averaged:hr,heartBPM != displayed{heartBPM=displayed}
        // The original packet is already durable before this callback. Derived
        // only the current display stays in memory.
        readingBuffer.append(point);if readingBuffer.count>1000{readingBuffer.removeFirst(readingBuffer.count-1000)}
        if Date().timeIntervalSince(lastChartUpdate)>=0.2{lastChartUpdate=Date();readings=Self.displayReadings(readingBuffer,average:current==nil)}
        gym.accept(point,manualWorkout:current != nil)
    }
    nonisolated static func displayReadings(_ source:[Reading],average:Bool=false)->[Reading]{
        let values=average ? FiveReadingAverage.series(source):source
        var segments=[Int](repeating:0,count:values.count)
        if values.count>1{for i in 1..<values.count{segments[i]=segments[i-1]+(values[i].date.timeIntervalSince(values[i-1].date)>8 ? 1:0)}}
        return ChartSeries.indices(values:values.map{Double($0.bpm)},limit:150).map{i in var p=values[i];p.segment=segments[i];return p}
    }
    private func receiveRecorded(_ value:RecordedResult){
        readingBuffer.removeAll{$0.date.timeIntervalSince1970>=value.startedAt && $0.date.timeIntervalSince1970<=value.availableUntil}
        readingBuffer += value.acceptedReadings
        readingBuffer.sort{$0.date<$1.date};if readingBuffer.count>3000{readingBuffer.removeFirst(readingBuffer.count-3000)}
        readings=Self.displayReadings(readingBuffer,average:current==nil);api.upload()
    }
    func recordPayload(_ session:WorkoutSession,action:String)->JSONValue {
        var value:[String:JSONValue]=["action":.string(action),"session_id":.string(session.remoteID ?? session.id.uuidString),"activity_name":.string(session.activityName),"active_seconds":.number(session.elapsed()),"auto_detected":.bool(session.auto_detected),"provisional":.bool(session.provisional),"source":.string(session.source)]
        value["config_snapshot"]=(try? JSONValue.encoded(session.config)) ?? .null
        value["profile_snapshot"]=(try? JSONValue.encoded(session.profile)) ?? .null
        value["phase_index"] = .number(Double(WorkoutEngine.position(elapsed:session.elapsed(),config:session.config).index))
        return .object(value)
    }
    private func changeWorkout(_ operation:@escaping @MainActor ()async throws->Void){
        guard !changingWorkout else{return};changingWorkout=true;workoutRevision+=1
        Task{defer{changingWorkout=false};do{try await operation()}catch{message=error.localizedDescription}}
    }
    private func commit(_ session:WorkoutSession,action:String,active:Bool=true)async throws{
        let payload=recordPayload(session,action:action)
        let archive=try await store.perform{store in
            try store.workout(session,payload:payload,active:active)
            return (store.sessions(),store.pendingCount)
        }
        if active{current=["running","paused"].contains(session.state) ? session:nil;readings=Self.displayReadings(readingBuffer,average:current==nil)}
        sessions=archive.0;pending=archive.1;api.upload()
    }
    func startWorkout(auto:Bool=false,id:UUID=UUID(),at date:Date=Date()){
        changeWorkout{[self] in
            try settings.config.validate();try settings.profile.validate()
            if var active=current {
                if !auto && active.auto_detected && active.provisional{active.provisional=false;active.source="manual";active.activityName=settings.config.activity_name?.trimmingCharacters(in:.whitespacesAndNewlines).nilIfEmpty ?? active.activityName;try await commit(active,action:"activity_updated");gym.resolve()}
                return
            }
            var session=WorkoutSession(id:id,activityName:settings.config.activity_name?.nilIfEmpty ?? (auto ? "Attività da confermare":"Allenamento"),startedAt:date,config:settings.config,profile:settings.profile,runningSince:date)
            session.auto_detected=auto;session.provisional=auto;session.source=auto ? "geofence_heart_rate":"manual"
            try await commit(session,action:"started");if !auto{gym.resolve()}
        }
    }
    func pause(){changeWorkout{[self] in guard var session=current,session.state=="running" else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.state="paused";try await commit(session,action:"paused")}}
    func resume(){changeWorkout{[self] in guard var session=current,session.state=="paused" else{return};session.runningSince=Date();session.state="running";try await commit(session,action:"resumed")}}
    func end(){changeWorkout{[self] in guard var session=current else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.endedAt=Date();session.state="ended";try await commit(session,action:"ended");gym.resolve();if !continuousCardio{Task{await ring.stopSport()}}}}
    func resolveCandidate(id:UUID,name:String?,negative:Bool){
        changeWorkout{[self] in
            guard var session=(current?.id==id ? current:sessions.first{$0.id==id}),session.auto_detected else{return}
            if negative {
                guard session.state != "false_detection" else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.state="false_detection";session.endedAt=Date();session.provisional=false
            }else{guard let text=name?.trimmingCharacters(in:.whitespacesAndNewlines),!text.isEmpty,text.count<=80 else{return};session.activityName=text;session.provisional=false}
            let active=current?.id==id;try await commit(session,action:negative ? "false_detection":"activity_updated",active:active);gym.resolve()
        }
    }
    func startRingSport(recordedOnly:Bool=false)async{guard let selected=settings.config.activity_id,allowedActivities.contains(where:{$0.0==selected}),(0...65535).contains(selected),let age=settings.profile.age,let rest=settings.profile.resting_hr else{message="Scegli un’attività compatibile e inserisci età e FC a riposo nel profilo";return}
        do{
            if recordedOnly,current==nil,!continuousCardio {
                guard !changingWorkout else{throw AppError.message("Salvataggio allenamento in corso")}
                let session=WorkoutSession(id:UUID(),activityName:settings.config.activity_name?.nilIfEmpty ?? "Allenamento",startedAt:Date(),config:settings.config,profile:settings.profile,runningSince:Date())
                try await commit(session,action:"started")
            }
            if recordedOnly,ring.recordedWorkoutProbeActive{return}
            try await ring.startSport(activity:UInt16(selected),age:age,resting:rest,recordedOnly:recordedOnly)
            pendingSportStart=nil
        }catch is OuraStartDeferred{pendingSportStart=recordedOnly;pendingSportUntil=Date().addingTimeInterval(900);continuousStatus="Allineamento storico · avvio automatico appena pronto";nextContinuousAttempt = .distantPast}
        catch{pendingSportStart=nil;message=error.localizedDescription}
    }
    func stopRingSport()async {
        pendingSportStart=nil
        await ring.stopSport()
        if continuousCardio{continuousStatus="Completo il segmento · ripresa automatica";nextContinuousAttempt=Date().addingTimeInterval(5)}
    }
    func setContinuousCardio(_ enabled:Bool)async {
        do{
            try await store.perform{try $0.save(enabled,key:"cardio.continuous.enabled")}
            continuousCardio=enabled;recorded.continuousMode=enabled;nextContinuousAttempt = .distantPast
            if !enabled{pendingSportStart=nil}
            if !enabled,current==nil{await ring.stopSport()}
            continuousStatus=enabled ? "Cardio esteso attivo · diario separato":"Cardio esteso disattivato"
            maintainContinuousCardio()
        }catch{message="Preferenza cardio non salvata"}
    }
    private func maintainContinuousCardio(){
        guard (continuousCardio || (pendingSportStart != nil && Date()<pendingSportUntil)),!controllingContinuous,!changingWorkout,ring.ready,!ring.busy,!ring.spo2ProbeActive,Date()>=nextContinuousAttempt else{return}
        nextContinuousAttempt=Date().addingTimeInterval(10);controllingContinuous=true
        Task{defer{controllingContinuous=false}
            // Charging is an explicit ring state. Missing HR is not a wear sensor.
            if batteryChargeState?.isCharging == true {
                continuousStatus="Cardio esteso sospeso durante la carica"
                if ring.recordedWorkoutProbeActive{await ring.stopSport()};return
            }
            if ring.recordedWorkoutProbeActive {
                if ContinuousCardioPolicy.needsRotation(recorded.result,at:Date()){
                    continuousStatus="Salvataggio segmento cardio · ripresa automatica";await ring.stopSport()
                }else{continuousStatus="Cardio esteso attivo · diario separato"}
                return
            }
            guard recorded.canPrepare else{return}
            if recorded.hasContext,recorded.result?.final != true {
                recorded.historyChanged()
                if ring.historyDataDrained,(try? await ring.activityIsInactive()) == true,await recorded.releaseInactiveCapture(){}else{continuousStatus="Completo il segmento cardio prima di riprendere";return}
            }
            guard let age=settings.profile.age,let resting=settings.profile.resting_hr else{continuousStatus="Completa il profilo per il cardio esteso";return}
            let activity=current?.config.activity_id ?? settings.config.activity_id ?? 65535
            guard (0...65535).contains(activity) else{return}
            do{try await ring.startSport(activity:UInt16(activity),age:age,resting:resting,recordedOnly:pendingSportStart ?? true);pendingSportStart=nil;continuousStatus="Cardio esteso attivo · diario separato"}
            catch{continuousStatus=error.localizedDescription;nextContinuousAttempt=Date().addingTimeInterval(30)}
        }
    }
    func regionEvent(_ inside:Bool){
        let payload:JSONValue = .object(["source":.string("geofence"),"gym_id":.string(settings.auto_detection.gym.id),"event":.string(inside ? "location_arrival":"location_exit")]),date=Date(),api=api
        store.enqueue({try $0.append(kind:"device_status",payload:payload,at:date);api.upload()},onError:{[weak self] _ in Task{@MainActor in self?.message="Evento palestra non salvato"}})
    }
    func saveSettings()async{
        do{try settings.profile.validate();try settings.config.validate()
            for rule in settings.weekly_schedule{
                let start=rule.start.split(separator:":").compactMap{Int($0)},end=rule.end.split(separator:":").compactMap{Int($0)}
                guard (1...7).contains(rule.weekday),start.count==2,end.count==2,(0...23).contains(start[0]),(0...59).contains(start[1]),(0...23).contains(end[0]),(0...59).contains(end[1]),end[0]*60+end[1]>start[0]*60+start[1],TimeZone(identifier:rule.timezone) != nil else{throw AppError.message("Controlla giorno e orari: la fine deve seguire l’inizio nello stesso giorno")}
            }
            guard (1...100).contains(settings.auto_detection.heart_rate_delta_bpm),(5...600).contains(settings.auto_detection.sustain_seconds),(60...86400).contains(settings.auto_detection.cooldown_seconds) else{throw AppError.message("Controlla i parametri di rilevamento")}
            settings.dirty=true;settings.revision=UUID().uuidString;let snapshot=settings;try await store.perform{try $0.save(snapshot,key:"settings")};persistedSettings=snapshot;gym.apply(settings.auto_detection)
            guard enrolled else{message="Impostazioni salvate sul telefono";return}
            try await pushSettings();message="Impostazioni salvate"
        }catch{message="Salvataggio remoto rinviato: \(error.localizedDescription)"}
    }
    private func setEnergySex(_ sex:String)async{
        guard ["male","female"].contains(sex) else{return}
        do{
            settings.profile.energy_sex=sex;try settings.profile.validate()
            settings.dirty=true;settings.revision=UUID().uuidString;let saved=settings
            try await store.perform{try $0.save(saved,key:"settings")};persistedSettings=saved
            if enrolled{try await pushSettings()}
        }catch{syncStatus="Preferenza energia salvata; invio da riprovare"}
    }
    private func pushSettings()async throws{
        let snapshot=settings,revision=settings.revision
        let value:JSONValue = .object(["profile":try JSONValue.encoded(snapshot.profile),"config":try JSONValue.encoded(snapshot.config),"weekly_schedule":try JSONValue.encoded(snapshot.weekly_schedule),"auto_detection":try JSONValue.encoded(snapshot.auto_detection)])
        let response=try await api.request(path:"mobile/settings",method:"PATCH",body:value)
        if let sex=snapshot.profile.energy_sex,response["settings"]["profile"]["energy_sex"].string != sex{syncStatus="Parametro calorie salvato; attendo l’aggiornamento FamilOS";try await applyBootstrap(response);return}
        if settings.revision==revision{var clean=snapshot;clean.dirty=false;let saved=clean;try await store.perform{try $0.save(saved,key:"settings")};if settings.revision==revision{settings.dirty=false;persistedSettings=saved}}
        try await applyBootstrap(response)
    }
    func enroll(code:String,server:String)async{
        do{guard let url=URL(string:server) else{throw AppError.message("Indirizzo server non valido")};let base=try WellnessAPI.base(url)
            let response=try await api.request(path:"bridges/enroll",method:"POST",body:.object(["pairing_code":.string(code.trimmingCharacters(in:.whitespacesAndNewlines)),"device_name":.string("Home Pulse · iPhone")]),credentials:nil,base:base)
            guard let device=response["device_id"].string,let token=response["access_token"].string,!token.isEmpty else{throw AppError.message("Risposta associazione incompleta")}
            try SecureStore.write(JSONEncoder().encode(Credentials(device_id:device,access_token:token,server:base)),key:"bridge")
            ownerName=response["owner_name"].string;let owner=ownerName;try await store.perform{try $0.save(owner,key:"ownerName")};enrolled=true
            try await applyBootstrap(response);await refresh();api.upload()
        }catch{message=error.localizedDescription}
    }
    func openPairingURL(_ url:URL)async{
        guard url.scheme=="familos-homepulse",url.host=="pair",let components=URLComponents(url:url,resolvingAgainstBaseURL:false),let code=components.queryItems?.first(where:{$0.name=="code"})?.value,let server=components.queryItems?.first(where:{$0.name=="server"})?.value else{message="Codice di associazione non riconosciuto";return}
        await enroll(code:code,server:server)
    }
    func refresh()async{
        guard enrolled,!refreshing else{return};refreshing=true;defer{refreshing=false};api.upload()
        do{
            try await syncDiaryCleanup()
            if settings.dirty{try await pushSettings()}else{try await applyBootstrap(await api.request(path:"mobile/bootstrap"))}
            if let analysis=try? await api.request(path:"mobile/analysis"){
                analysisData=analysis;refreshChartCache()
            }
            syncStatus="Dati FamilOS aggiornati";api.upload()
        }catch{syncStatus="Offline · impostazioni e allenamenti disponibili"}
    }
    func applyDiaryCleanup(_ rule:DiaryCleanup,pending:Bool=false)async throws{
        let value=try await store.perform{store in try store.applyDiaryCleanup(rule,pending:pending);return(store.sessions(),store.load(WorkoutSession.self,key:"activeWorkout"))}
        diaryDiagnostics.submit(.object(["at":.number(Date().timeIntervalSince1970),"cleanup_applied":.bool(true),"visible_sessions":.number(Double(value.0.count)),"all_visible_sessions_allowed":.bool(value.0.allSatisfy{rule.allows($0)}),"retained_window_sessions":.number(Double(value.0.filter{$0.startedAt.timeIntervalSince1970<=rule.retain_end && ($0.endedAt?.timeIntervalSince1970 ?? $0.startedAt.timeIntervalSince1970+$0.accumulated)>=rule.retain_start}.count)),"sensor_data_preserved":.bool(true)]))
        sessions=value.0;if let current,!rule.allows(current){self.current=nil;gym.resolve()};if current==nil{current=value.1}
        var data=serverData.object;data["sessions"] = .array(serverData["sessions"].array.filter{item in SessionReconciliation.decode(item,receivedAt:Date()).map{rule.allows($0)} ?? false});serverData = .object(data)
    }
    private func syncDiaryCleanup()async throws{
        guard let rule=try await store.perform({$0.load(DiaryCleanup.self,key:"diary.cleanup.pending")}) else{return}
        do{_ = try await api.request(path:"mobile/diary/cleanup",method:"POST",body:try JSONValue.encoded(rule));try await store.perform{try $0.save(Optional<DiaryCleanup>.none,key:"diary.cleanup.pending")}}
        catch{ /* Keep the request durable while the server update is pending. */ }
    }
    func diaryAnalysis(hours:Int)async->JSONValue{
        if enrolled,let value=try? await api.request(path:"mobile/diary",query:[URLQueryItem(name:"hours",value:String(hours))]){return value}
        let end=Date(),start=Date().addingTimeInterval(-Double(hours)*3600)
        return (try? await store.perform{$0.diaryLocal(start:start,end:end)}) ?? .null
    }
    func workoutAnalysis(_ session:WorkoutSession)async->JSONValue{
        let remote=await sessionAnalysis(session.remoteID ?? session.id.uuidString)
        if remote != .null{return remote}
        return (try? await store.perform{$0.diaryLocal(start:session.startedAt,end:session.endedAt ?? Date())}) ?? .null
    }
    func refreshSleep()async{
        guard enrolled,!refreshingSleep else{return};refreshingSleep=true;defer{refreshingSleep=false}
        do{let value=try await api.request(path:"mobile/sleep",query:[URLQueryItem(name:"days",value:"14")]);sleepData=value;sleepStatus=value["processing_error"].string ?? "";sleepRefreshSchedule.completed(at:Date(),succeeded:true)
            // One overwritten operational snapshot, without sample values or
            // phase arrays; the phone does not retain a second sleep archive.
            let summaries=value["periods"].array.prefix(32).map{period in JSONValue.object([
                "started_at":period["started_at"],"ended_at":period["ended_at"],"source":period["source"],
                "has_total":.bool(period["total_sleep_seconds"] != .null),"has_stages":.bool(period["stage_seconds"] != .null),
                "phase_count":.number(Double(period["phases_30_seconds"].string?.count ?? 0)),
                "spo2_samples":period["spo2"]["sample_count"],"breathing_samples":period["respiratory_rate"]["sample_count"]])}
            sleepDiagnostics.submit(.object(["at":.number(Date().timeIntervalSince1970),"processing_status":value["processing_status"],"processing_error":value["processing_error"],"last_processed_at":value["last_processed_at"],"period_count":.number(Double(value["periods"].array.count)),"periods":.array(Array(summaries))]))
        }
        catch{sleepStatus=sleepData == .null ? "Sonno non disponibile: riprova quando il server è raggiungibile":"Ultimi dati di questa apertura · aggiornamento rinviato";sleepRefreshSchedule.completed(at:Date(),succeeded:false)}
    }
    private func decode<T:Decodable>(_ type:T.Type,_ value:JSONValue)->T?{guard value != .null,let d=try? JSONEncoder().encode(value) else{return nil};return try? JSONDecoder().decode(type,from:d)}
    private var unsavedSettings:Bool{
        let saved=persistedSettings
        return settings.profile != saved.profile || settings.config != saved.config || settings.weekly_schedule != saved.weekly_schedule || settings.auto_detection != saved.auto_detection
    }
    func applyBootstrap(_ response:JSONValue)async throws{
        for value in response["diary_cleanups"].array{if let rule=decode(DiaryCleanup.self,value),rule.valid{try await applyDiaryCleanup(rule)}}

        if let binding=decode(RingBinding.self,response["ring_binding"]){
            if let hex=binding.owner_key_hex,let key=Data(hex:hex),key.count==16{try SecureStore.write(key,key:"ring.\(binding.logical_ring_id)")}
            try await store.perform{try $0.save(binding.logical_ring_id,key:"logicalRing")};ring.configure(logicalRing:binding.logical_ring_id)
        }
        let remote=response["settings"]
        if !settings.dirty,!unsavedSettings,remote != .null {
            if let value=decode(BodyProfile.self,remote["profile"]){settings.profile=value}
            if let value=decode(WorkoutConfig.self,remote["config"]){settings.config=value}
            if let value=decode([WeeklyRule].self,remote["weekly_schedule"]){settings.weekly_schedule=value}
            if let value=decode(AutoDetectionSettings.self,remote["auto_detection"]){settings.auto_detection=value}
            let saved=settings;try await store.perform{try $0.save(saved,key:"settings")};persistedSettings=saved;gym.apply(settings.auto_detection)
        }
        let catalog:[(Int,String)]=response["allowed_activities"].array.compactMap{v in guard let id=v["id"].number,let name=v["name"].string else{return nil};return(Int(id),name)}
        if !catalog.isEmpty{allowedActivities=catalog}
        if !changingWorkout{
            let revision=workoutRevision
            let rules=try await store.perform{$0.load([DiaryCleanup].self,key:"diary.cleanups") ?? []}
            let imported=response["sessions"].array.compactMap{SessionReconciliation.decode($0,receivedAt:Date())}.filter{session in rules.allSatisfy{$0.allows(session)}}
            let active=SessionReconciliation.decode(response["active_session"],receivedAt:Date()).flatMap{session in rules.allSatisfy{$0.allows(session)} ? session:nil}
            let matching=current.flatMap{local in imported.first{$0.id==local.id}}
            // Un’assenza non equivale a una conclusione: serve uno stato esplicito.
            let next=active ?? (matching != nil ? nil:current)
            let wasActive=current != nil
            let archive=try await store.perform{store in (try store.reconcileSessions(imported,active:next),store.sessions())}
            if archive.0,!changingWorkout,revision==workoutRevision{current=next;sessions=archive.1;if wasActive,next==nil{gym.resolve()}}
        }
        var safe=response.object;safe.removeValue(forKey:"ring_binding");safe.removeValue(forKey:"access_token");safe.removeValue(forKey:"device_id")
        let diaryRules=try await store.perform{$0.load([DiaryCleanup].self,key:"diary.cleanups") ?? []}
        if !diaryRules.isEmpty{safe["sessions"] = .array(response["sessions"].array.filter{value in SessionReconciliation.decode(value,receivedAt:Date()).map{session in diaryRules.allSatisfy{$0.allows(session)}} ?? false})}
        let bootstrap=JSONValue.object(safe);serverData=bootstrap;recorded.observeServer(bootstrap["recorded_cardio"]);refreshChartCache()
    }
    private func refreshChartCache(){
        chartRevision+=1;let revision=chartRevision,analysis=analysisData,bootstrap=serverData
        Task{[weak self] in
            let charts=await Task.detached(priority:.utility){ChartSeries.build(analysis:analysis,bootstrap:bootstrap)}.value
            guard let self,chartRevision==revision else{return};chartCache=charts
        }
    }
    func serverPoints(_ metric:String)->[ChartPoint]{chartCache[metric] ?? []}
    nonisolated static func chartPoints(_ value:JSONValue)->[ChartPoint]{ChartSeries.bound(ChartSeries.decode(value))}
    func sessionAnalysis(_ id:String)async->JSONValue{
        guard enrolled else{return .null}
        return (try? await api.request(path:"mobile/analysis",query:[URLQueryItem(name:"session_id",value:id)])) ?? .null
    }

}
struct ChartPoint:Identifiable,Sendable {var date:Date;var value:Double;var segment:Int?=nil;var id:String{"\(date.timeIntervalSince1970.bitPattern)-\(value.bitPattern)"}}
enum ChartSeries {
    static func decode(_ value:JSONValue)->[ChartPoint]{
        let points:[ChartPoint]=value.array.compactMap{v in guard let t=v["t"].number,let number=v["v"].number,t.isFinite,number.isFinite else{return nil};return ChartPoint(date:Date(timeIntervalSince1970:t),value:number,segment:v["segment"].number.map{Int($0)})}
        var previous:Date?;var segment=0
        return points.sorted{$0.date<$1.date}.map{point in
            var point=point
            if point.segment==nil{if let previous,point.date.timeIntervalSince(previous)>8{segment+=1};point.segment=segment}
            previous=point.date;return point
        }
    }
    static func build(analysis:JSONValue,bootstrap:JSONValue)->[String:[ChartPoint]]{
        var result:[String:[ChartPoint]]=[:]
        for metric in ["heart_rate","temperature","spo2","respiratory_rate","motion","step_increments"]{
            var points=decode(analysis[metric]);let latest=bootstrap["latest"][metric]
            if let time=latest["captured_at"].number,let value=latest["value"].number,time.isFinite,value.isFinite,!points.contains(where:{$0.date.timeIntervalSince1970==time}){points.append(ChartPoint(date:Date(timeIntervalSince1970:time),value:value))}
            result[metric]=bound(points.sorted{$0.date<$1.date})
        }
        return result
    }
    // Presentation only: retain real extrema and timestamps, with the full
    // original analysis stored separately for statistics and offline use.
    static func bound(_ points:[ChartPoint],limit:Int=300)->[ChartPoint]{
        indices(values:points.map{$0.value},limit:limit).map{points[$0]}
    }
    static func indices(values:[Double],limit:Int)->[Int]{
        guard values.count>limit,limit>=4 else{return Array(values.indices)}
        let buckets=(limit-2)/2,width=Double(values.count-2)/Double(buckets)
        var indices=Set([0,values.count-1])
        for bucket in 0..<buckets{
            let first=1+Int(Double(bucket)*width),end=min(values.count-1,1+Int(Double(bucket+1)*width))
            guard first<end else{continue}
            let range=first..<end
            if let low=range.min(by:{values[$0]<values[$1]}){indices.insert(low)}
            if let high=range.max(by:{values[$0]<values[$1]}){indices.insert(high)}
        }
        return indices.sorted()
    }
}
extension String {var nilIfEmpty:String?{trimmingCharacters(in:.whitespacesAndNewlines).isEmpty ? nil:self}}
