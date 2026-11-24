import Foundation
import Combine
import UserNotifications

@MainActor final class AppModel:ObservableObject {
    @Published var settings:LocalSettings
    @Published var current:WorkoutSession?
    @Published var sessions:[WorkoutSession]=[];@Published var readings:[Reading]=[]
    @Published var now=Date();@Published var bpm:Int?;@Published var sampleAt:Date?
    @Published var warnings:[String]=[];@Published var temperatureRaw:Int?
    @Published var metrics=HistoricalMetrics();@Published var serverData:JSONValue = .null
    @Published var analysisData:JSONValue = .null
    @Published var syncStatus="Offline pronto";@Published var message:String?
    @Published var ownerName:String?;@Published var enrolled=false;@Published var pending=0
    @Published var allowedActivities:[(Int,String)]=ActivityCatalog.defaults
    @Published var batteryLevel:Int?;@Published var batteryAt:Date?
    @Published var batteryNotificationStatus="Notifiche da autorizzare"
    private var batteryAlert=BatteryAlertState();private var batteryNotificationPending=false
    let store:EventStore;let api:WellnessAPI;let ring:RingBridge;let gym:GymDetection
    private var ticking:Task<Void,Never>?;private var refreshing=false
    init(store:EventStore,folder:URL){
        self.store=store
        settings=store.load(LocalSettings.self,key:"settings") ?? LocalSettings()
        api=WellnessAPI(store:store,folder:folder.appendingPathComponent("uploads"))
        ring=RingBridge(store:store);gym=GymDetection(store:store)
        enrolled=SecureStore.credentials() != nil
        if let active=store.load(WorkoutSession.self,key:"activeWorkout"),["running","paused"].contains(active.state){current=active}
        sessions=store.sessions();readings=store.readings();metrics=store.metrics()
        serverData=store.load(JSONValue.self,key:"bootstrap") ?? .null
        analysisData=store.load(JSONValue.self,key:"analysis.last24h") ?? .null
        ownerName=store.load(String.self,key:"ownerName")
        if let logical=store.load(String.self,key:"logicalRing"){ring.configure(logicalRing:logical)}
        gym.apply(settings.auto_detection)
        api.onStatus={[weak self] status in Task{@MainActor in self?.syncStatus=status}}
        api.onUpload={[weak self] in Task{@MainActor in self?.pending=self?.store.pendingCount ?? 0}}
        api.onAcknowledged={[weak self] in Task{@MainActor in await self?.refresh()}}
        ring.onLive={[weak self] value,time in self?.receive(value,at:time)}
        ring.onHistory={[weak self] _ in self?.metrics=self?.store.metrics() ?? HistoricalMetrics()}
        ring.onJournal={[weak self] in self?.pending=self?.store.pendingCount ?? 0}
        gym.onCandidate={[weak self] id,date in self?.startWorkout(auto:true,id:id,at:date)}
        gym.onReply={[weak self] id,name,negative in self?.resolveCandidate(id:id,name:name,negative:negative)}
        gym.onRegion={[weak self] inside in self?.regionEvent(inside)}
        if let snapshot=store.load(RingBatterySnapshot.self,key:"ringBattery.latest"){batteryLevel=snapshot.level;batteryAt=snapshot.at}
        batteryAlert=store.load(BatteryAlertState.self,key:"ringBattery.alert") ?? BatteryAlertState()
        ring.onBattery={[weak self] level,date in self?.receiveBattery(level,at:date)}
        Task{await refreshBatteryNotificationStatus()}
        pending=store.pendingCount
        if current==nil,let id=gym.engine.state.candidateSessionID,let date=gym.engine.state.candidateAt{startWorkout(auto:true,id:id,at:date)}
        ticking=Task{[weak self] in var ticks=0;while !Task.isCancelled{try? await Task.sleep(nanoseconds:1_000_000_000);guard let self else{return};now=Date();ring.maintenance();ticks+=1;if ticks%30==0{await refresh();pending=store.pendingCount}}}
    }
    func refreshBatteryNotificationStatus()async{
        let permission=await UNUserNotificationCenter.current().notificationSettings()
        batteryNotificationStatus=permission.authorizationStatus == .authorized || permission.authorizationStatus == .provisional ? "Avviso al 10% attivo":"Notifiche da autorizzare nelle impostazioni iPhone"
    }
    func requestBatteryNotifications()async{
        do{_ = try await UNUserNotificationCenter.current().requestAuthorization(options:[.alert,.sound]);await refreshBatteryNotificationStatus()
            if let level=batteryLevel,let date=batteryAt,Date().timeIntervalSince(date)<600{receiveBattery(level,at:date)}
        }catch{message="Autorizzazione notifiche non completata"}
    }
    private func receiveBattery(_ level:Int,at date:Date){
        guard (0...100).contains(level) else{return}
        batteryLevel=level;batteryAt=date
        do{try store.save(RingBatterySnapshot(level:level,at:date),key:"ringBattery.latest")}catch{message="Livello batteria non salvato"}
        if level>=20{_ = batteryAlert.observe(level);try? store.save(batteryAlert,key:"ringBattery.alert")}
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
                _ = batteryAlert.observe(latest);try store.save(batteryAlert,key:"ringBattery.alert")
            }catch{message="Avviso batteria non completato"}
        }
    }
    var displayedBPM:Int?{guard let sampleAt,now.timeIntervalSince(sampleAt)>=0,now.timeIntervalSince(sampleAt)<=8 else{return nil};return bpm}
    private func receive(_ value:OuraLive,at date:Date){warnings=value.warnings;temperatureRaw=value.temperatureRaw
        guard let hr=value.bpm else{bpm=nil;return}
        guard OuraWire.displaySupported(hr) else{bpm=nil;warnings.append("Battito fuori intervallo di visualizzazione: pacchetto conservato");return};bpm=hr;sampleAt=date
        let point=Reading(date:date,bpm:hr)
        do{try store.addReading(point);readings.append(point);if readings.count>1000{readings.removeFirst(readings.count-1000)}
            gym.accept(point,manualWorkout:current != nil)
        }catch{message="Lettura non salvata; controlla lo spazio disponibile"}
    }
    func recordPayload(_ session:WorkoutSession,action:String)->JSONValue {
        var value:[String:JSONValue]=["action":.string(action),"session_id":.string(session.remoteID ?? session.id.uuidString),"activity_name":.string(session.activityName),"active_seconds":.number(session.elapsed()),"auto_detected":.bool(session.auto_detected),"provisional":.bool(session.provisional),"source":.string(session.source)]
        value["config_snapshot"]=(try? JSONValue.encoded(session.config)) ?? .null
        value["profile_snapshot"]=(try? JSONValue.encoded(session.profile)) ?? .null
        value["phase_index"] = .number(Double(WorkoutEngine.position(elapsed:session.elapsed(),config:session.config).index))
        return .object(value)
    }
    func commit(_ session:WorkoutSession,action:String,active:Bool=true)throws{
        try store.workout(session,payload:recordPayload(session,action:action),active:active)
        if active{current=["running","paused"].contains(session.state) ? session:nil}
        sessions=store.sessions();pending=store.pendingCount;api.upload()
    }
    func startWorkout(auto:Bool=false,id:UUID=UUID(),at date:Date=Date()){
        do{try settings.config.validate();try settings.profile.validate()
            if var active=current {
                if !auto && active.auto_detected && active.provisional{active.provisional=false;active.source="manual";active.activityName=settings.config.activity_name?.trimmingCharacters(in:.whitespacesAndNewlines).nilIfEmpty ?? active.activityName;try commit(active,action:"activity_updated");gym.resolve()}
                return
            }
            var session=WorkoutSession(id:id,activityName:settings.config.activity_name?.nilIfEmpty ?? (auto ? "Attività da confermare":"Allenamento"),startedAt:date,config:settings.config,profile:settings.profile,runningSince:date)
            session.auto_detected=auto;session.provisional=auto;session.source=auto ? "geofence_heart_rate":"manual"
            try commit(session,action:"started");if !auto{gym.resolve()}
        }catch{message=error.localizedDescription}
    }
    func pause(){guard var session=current,session.state=="running" else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.state="paused";do{try commit(session,action:"paused")}catch{message=error.localizedDescription}}
    func resume(){guard var session=current,session.state=="paused" else{return};session.runningSince=Date();session.state="running";do{try commit(session,action:"resumed")}catch{message=error.localizedDescription}}
    func end(){guard var session=current else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.endedAt=Date();session.state="ended";do{try commit(session,action:"ended");gym.resolve();Task{await ring.stopSport()}}catch{message=error.localizedDescription}}
    func resolveCandidate(id:UUID,name:String?,negative:Bool){
        guard var session=(current?.id==id ? current:store.sessions().first{$0.id==id}),session.auto_detected else{return}
        if negative {
            guard session.state != "false_detection" else{return};session.accumulated=session.elapsed();session.runningSince=nil;session.state="false_detection";session.endedAt=Date();session.provisional=false
        }else{guard let text=name?.trimmingCharacters(in:.whitespacesAndNewlines),!text.isEmpty,text.count<=80 else{return};session.activityName=text;session.provisional=false}
        do{let active=current?.id==id;try commit(session,action:negative ? "false_detection":"activity_updated",active:active);gym.resolve()}catch{message=error.localizedDescription}
    }
    func startRingSport()async{guard let selected=settings.config.activity_id,allowedActivities.contains(where:{$0.0==selected}),(0...65535).contains(selected),let age=settings.profile.age,let rest=settings.profile.resting_hr else{message="Scegli un’attività compatibile e inserisci età e FC a riposo nel profilo";return}
        do{try await ring.startSport(activity:UInt16(selected),age:age,resting:rest)}catch{message=error.localizedDescription}
    }
    func regionEvent(_ inside:Bool){do{try store.append(kind:"device_status",payload:.object(["source":.string("geofence"),"gym_id":.string(settings.auto_detection.gym.id),"event":.string(inside ? "location_arrival":"location_exit")]));pending=store.pendingCount;api.upload()}catch{message="Evento palestra non salvato"}}
    func saveSettings()async{
        do{try settings.profile.validate();try settings.config.validate()
            for rule in settings.weekly_schedule{
                let start=rule.start.split(separator:":").compactMap{Int($0)},end=rule.end.split(separator:":").compactMap{Int($0)}
                guard (1...7).contains(rule.weekday),start.count==2,end.count==2,(0...23).contains(start[0]),(0...59).contains(start[1]),(0...23).contains(end[0]),(0...59).contains(end[1]),end[0]*60+end[1]>start[0]*60+start[1],TimeZone(identifier:rule.timezone) != nil else{throw AppError.message("Controlla giorno e orari: la fine deve seguire l’inizio nello stesso giorno")}
            }
            guard (1...100).contains(settings.auto_detection.heart_rate_delta_bpm),(5...600).contains(settings.auto_detection.sustain_seconds),(60...86400).contains(settings.auto_detection.cooldown_seconds) else{throw AppError.message("Controlla i parametri di rilevamento")}
            settings.dirty=true;settings.revision=UUID().uuidString;try store.save(settings,key:"settings");gym.apply(settings.auto_detection)
            guard enrolled else{message="Impostazioni salvate sul telefono";return}
            try await pushSettings();message="Impostazioni salvate"
        }catch{message="Salvataggio remoto rinviato: \(error.localizedDescription)"}
    }
    private func pushSettings()async throws{
        let snapshot=settings,revision=settings.revision
        let value:JSONValue = .object(["profile":try JSONValue.encoded(snapshot.profile),"config":try JSONValue.encoded(snapshot.config),"weekly_schedule":try JSONValue.encoded(snapshot.weekly_schedule),"auto_detection":try JSONValue.encoded(snapshot.auto_detection)])
        let response=try await api.request(path:"mobile/settings",method:"PATCH",body:value)
        if settings.revision==revision{settings.dirty=false;try store.save(settings,key:"settings")}
        try applyBootstrap(response)
    }
    func enroll(code:String,server:String)async{
        do{guard let url=URL(string:server) else{throw AppError.message("Indirizzo server non valido")};let base=try WellnessAPI.base(url)
            let response=try await api.request(path:"bridges/enroll",method:"POST",body:.object(["pairing_code":.string(code.trimmingCharacters(in:.whitespacesAndNewlines)),"device_name":.string("Home Pulse · iPhone")]),credentials:nil,base:base)
            guard let device=response["device_id"].string,let token=response["access_token"].string,!token.isEmpty else{throw AppError.message("Risposta associazione incompleta")}
            try SecureStore.write(JSONEncoder().encode(Credentials(device_id:device,access_token:token,server:base)),key:"bridge")
            ownerName=response["owner_name"].string;try store.save(ownerName,key:"ownerName");enrolled=true
            try applyBootstrap(response);await refresh();api.upload()
        }catch{message=error.localizedDescription}
    }
    func openPairingURL(_ url:URL)async{
        guard url.scheme=="familos-homepulse",url.host=="pair",let components=URLComponents(url:url,resolvingAgainstBaseURL:false),let code=components.queryItems?.first(where:{$0.name=="code"})?.value,let server=components.queryItems?.first(where:{$0.name=="server"})?.value else{message="Codice di associazione non riconosciuto";return}
        await enroll(code:code,server:server)
    }
    func refresh()async{guard enrolled,!refreshing else{return};refreshing=true;defer{refreshing=false};api.upload();do{if settings.dirty{try await pushSettings()}else{try applyBootstrap(await api.request(path:"mobile/bootstrap"))};if let analysis=try? await api.request(path:"mobile/analysis"){analysisData=analysis;try store.save(analysis,key:"analysis.last24h")};syncStatus="Dati FamilOS aggiornati";api.upload()}catch{syncStatus="Offline · impostazioni e allenamenti disponibili"}}
    private func decode<T:Decodable>(_ type:T.Type,_ value:JSONValue)->T?{guard value != .null,let d=try? JSONEncoder().encode(value) else{return nil};return try? JSONDecoder().decode(type,from:d)}
    private var unsavedSettings:Bool{
        guard let saved=store.load(LocalSettings.self,key:"settings") else{return false}
        return settings.profile != saved.profile || settings.config != saved.config || settings.weekly_schedule != saved.weekly_schedule || settings.auto_detection != saved.auto_detection
    }
    func applyBootstrap(_ response:JSONValue)throws{
        if let binding=decode(RingBinding.self,response["ring_binding"]){
            if let hex=binding.owner_key_hex,let key=Data(hex:hex),key.count==16{try SecureStore.write(key,key:"ring.\(binding.logical_ring_id)")}
            try store.save(binding.logical_ring_id,key:"logicalRing");ring.configure(logicalRing:binding.logical_ring_id)
        }
        let remote=response["settings"]
        if !settings.dirty,!unsavedSettings,remote != .null {
            if let value=decode(BodyProfile.self,remote["profile"]){settings.profile=value}
            if let value=decode(WorkoutConfig.self,remote["config"]){settings.config=value}
            if let value=decode([WeeklyRule].self,remote["weekly_schedule"]){settings.weekly_schedule=value}
            if let value=decode(AutoDetectionSettings.self,remote["auto_detection"]){settings.auto_detection=value}
            try store.save(settings,key:"settings");gym.apply(settings.auto_detection)
        }
        let catalog:[(Int,String)]=response["allowed_activities"].array.compactMap{v in guard let id=v["id"].number,let name=v["name"].string else{return nil};return(Int(id),name)}
        if !catalog.isEmpty{allowedActivities=catalog}
        if store.pendingWorkoutCount==0{
            let imported=response["sessions"].array.compactMap{SessionReconciliation.decode($0,receivedAt:Date())}
            let active=SessionReconciliation.decode(response["active_session"],receivedAt:Date())
            let matching=current.flatMap{local in imported.first{$0.id==local.id}}
            // Un’assenza non equivale a una conclusione: serve uno stato esplicito.
            let next=active ?? (matching != nil ? nil:current)
            let wasActive=current != nil
            try store.reconcileSessions(imported,active:next);current=next;sessions=store.sessions();if wasActive,next==nil{gym.resolve()}
        }
        var safe=response.object;safe.removeValue(forKey:"ring_binding");safe.removeValue(forKey:"access_token");safe.removeValue(forKey:"device_id")
        serverData = .object(safe);try store.save(serverData,key:"bootstrap")
    }
    func serverPoints(_ metric:String)->[ChartPoint]{
        var points=Self.chartPoints(analysisData[metric])
        let latest=serverData["latest"][metric]
        if let time=latest["captured_at"].number,let value=latest["value"].number,time.isFinite,value.isFinite,!points.contains(where:{$0.date.timeIntervalSince1970==time}){points.append(ChartPoint(date:Date(timeIntervalSince1970:time),value:value))}
        return points.sorted{$0.date<$1.date}
    }
    static func chartPoints(_ value:JSONValue)->[ChartPoint]{value.array.compactMap{v in guard let t=v["t"].number,let value=v["v"].number,t.isFinite,value.isFinite else{return nil};return ChartPoint(date:Date(timeIntervalSince1970:t),value:value)}}
    func sessionAnalysis(_ id:String)async->JSONValue{
        let key="analysis.session.\(id)"
        if enrolled {let items=[URLQueryItem(name:"session_id",value:id)]
            if let value=try? await api.request(path:"mobile/analysis",query:items){try? store.save(value,key:key);return value}
        }
        return store.load(JSONValue.self,key:key) ?? .null
    }

}
struct ChartPoint:Identifiable {var id=UUID();var date:Date;var value:Double}
extension String {var nilIfEmpty:String?{trimmingCharacters(in:.whitespacesAndNewlines).isEmpty ? nil:self}}
