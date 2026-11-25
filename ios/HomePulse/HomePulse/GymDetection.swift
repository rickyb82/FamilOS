import Foundation
import CoreLocation
import UserNotifications
import Combine

struct Gym:Codable,Equatable,Sendable {
    var id="serendip-prato";var name="Serendip Prato"
    var address="Via Giuseppe Valentini 20, 59100 Prato, Italia"
    var latitude:Double?;var longitude:Double?;var radius_m:Double=150
}
struct AutoDetectionSettings:Codable,Equatable,Sendable {
    var enabled=true;var gym=Gym();var heart_rate_delta_bpm=15
    var sustain_seconds=30;var cooldown_seconds=3600
    init(){}
    enum CodingKeys:String,CodingKey{case enabled,gym,heart_rate_delta_bpm,sustain_seconds,cooldown_seconds}
    init(from decoder:Decoder)throws{
        let c=try decoder.container(keyedBy:CodingKeys.self)
        enabled=(try? c.decode(Bool.self,forKey:.enabled)) ?? true
        gym=(try? c.decode(Gym.self,forKey:.gym)) ?? Gym()
        heart_rate_delta_bpm=(try? c.decode(Int.self,forKey:.heart_rate_delta_bpm)) ?? 15
        sustain_seconds=(try? c.decode(Int.self,forKey:.sustain_seconds)) ?? 30
        cooldown_seconds=(try? c.decode(Int.self,forKey:.cooldown_seconds)) ?? 3600
    }
}
struct TimedBPM:Codable,Equatable,Sendable {var at:Date;var bpm:Int}
struct DetectionState:Codable,Equatable,Sendable {
    var inside=false;var entry:Date?;var baseline:Double?;var baselineSamples:[TimedBPM]=[]
    var riseStart:Date?;var riseSamples=0;var lastGood:Date?;var cooldownUntil:Date?
    var candidateSessionID:UUID?;var candidateAt:Date?
}
struct GymDetectionEngine {
    var settings=AutoDetectionSettings();var state=DetectionState()
    mutating func enter(at now:Date,recent:[TimedBPM]) {
        guard !state.inside else{return};state.inside=true;state.entry=now
        state.riseStart=nil;state.riseSamples=0;state.lastGood=nil
        let samples=recent.filter{now.timeIntervalSince($0.at)>=0 && now.timeIntervalSince($0.at)<=60}
        state.baselineSamples=samples;state.baseline=samples.count>=6 ? Self.median(samples.map{Double($0.bpm)}):nil
    }
    mutating func exit(){state.inside=false;state.riseStart=nil;state.riseSamples=0}
    mutating func ingest(_ sample:TimedBPM,now:Date,manualWorkout:Bool)->UUID? {
        guard settings.enabled,state.inside,!manualWorkout,state.candidateSessionID==nil,
              now.timeIntervalSince(sample.at)>=0,now.timeIntervalSince(sample.at)<=8,
              OuraWire.displaySupported(sample.bpm) else{return nil}
        if let until=state.cooldownUntil,now<until{return nil}
        if let previous=state.lastGood,sample.at<=previous{return nil}
        if let previous=state.lastGood,sample.at.timeIntervalSince(previous)>10{state.riseStart=nil;state.riseSamples=0;if sample.at.timeIntervalSince(previous)>120{state.baseline=nil;state.baselineSamples=[]}}
        state.lastGood=sample.at
        if state.baseline==nil {
            state.baselineSamples.append(sample)
            state.baselineSamples=state.baselineSamples.filter{sample.at.timeIntervalSince($0.at)<=120}
            if state.baselineSamples.count>=6,let first=state.baselineSamples.first,sample.at.timeIntervalSince(first.at)>=30 {
                state.baseline=Self.median(state.baselineSamples.map{Double($0.bpm)})
            }
            return nil
        }
        guard Double(sample.bpm)>=state.baseline!+Double(settings.heart_rate_delta_bpm) else{state.riseStart=nil;state.riseSamples=0;return nil}
        if state.riseStart==nil{state.riseStart=sample.at};state.riseSamples+=1
        guard let start=state.riseStart,sample.at.timeIntervalSince(start)>=Double(settings.sustain_seconds),state.riseSamples>=6 else{return nil}
        let id=UUID();state.candidateSessionID=id;state.candidateAt=now
        state.cooldownUntil=now.addingTimeInterval(Double(settings.cooldown_seconds));state.riseStart=nil;state.riseSamples=0
        return id
    }
    mutating func resolve(){state.candidateSessionID=nil;state.candidateAt=nil;state.riseStart=nil;state.riseSamples=0}
    static func median(_ values:[Double])->Double{let a=values.sorted();return a.count%2==0 ? (a[a.count/2-1]+a[a.count/2])/2:a[a.count/2]}
}

@MainActor final class GymDetection:NSObject,ObservableObject,@preconcurrency CLLocationManagerDelegate,UNUserNotificationCenterDelegate {
    static let prompt="Sei in palestra e hai iniziato allenamento? Scrivi cosa stai facendo"
    @Published var engine=GymDetectionEngine()
    @Published var permission="Posizione non autorizzata";@Published var notificationPermission="Notifiche da autorizzare"
    @Published var location:CLLocationCoordinate2D?;@Published var status="Posizione palestra da verificare"
    var onCandidate:((UUID,Date)->Void)?;var onReply:((UUID,String?,Bool)->Void)?;var onRegion:((Bool)->Void)?
    private let manager=CLLocationManager();private let geocoder=CLGeocoder();private let store:EventStore
    private let center=UNUserNotificationCenter.current()
    private var savedCoordinates:[String:[Double]]=[:]
    init(store:EventStore,state:DetectionState,coordinates:[String:[Double]]){self.store=store;savedCoordinates=coordinates;super.init();manager.delegate=self;center.delegate=self
        engine.state=state
        let input=UNTextInputNotificationAction(identifier:"activityName",title:"Scrivi attività",options:[],textInputButtonTitle:"Salva",textInputPlaceholder:"Che attività stai facendo?")
        let no=UNNotificationAction(identifier:"notWorkout",title:"Non mi alleno",options:[])
        center.setNotificationCategories([UNNotificationCategory(identifier:"gymCandidate",actions:[input,no],intentIdentifiers:[],options:[])])
        updatePermissions()
    }
    func apply(_ settings:AutoDetectionSettings){engine.settings=settings
        if let lat=settings.gym.latitude,let lon=settings.gym.longitude{location=CLLocationCoordinate2D(latitude:lat,longitude:lon)}
        else if let saved=savedCoordinates[settings.gym.id],saved.count==2{location=CLLocationCoordinate2D(latitude:saved[0],longitude:saved[1])}
        else{location=nil}
        monitor()
    }
    func requestPermissions(){manager.requestAlwaysAuthorization();Task{_ = try? await center.requestAuthorization(options:[.alert,.sound,.badge]);updatePermissions()}}
    func updatePermissions(){
        permission=manager.authorizationStatus == .authorizedAlways ? "Posizione: sempre":manager.authorizationStatus == .authorizedWhenInUse ? "Posizione: durante l’uso":"Posizione da autorizzare"
        Task{let s=await center.notificationSettings();notificationPermission=s.authorizationStatus == .authorized || s.authorizationStatus == .provisional ? "Notifiche autorizzate":"Notifiche da autorizzare"}
    }
    func locateGym()async{guard location==nil else{monitor();return};status="Ricerca indirizzo palestra…"
        do{let places=try await geocoder.geocodeAddressString(engine.settings.gym.address)
            guard let coordinate=places.first?.location?.coordinate else{throw AppError.message("Indirizzo non individuato: scegli il punto sulla mappa")}
            try setCoordinate(coordinate);status="Controlla il punto della palestra sulla mappa"
        }catch{status="Palestra non individuata: puoi impostare la posizione manualmente"}
    }
    func setCoordinate(_ coordinate:CLLocationCoordinate2D)throws{
        guard CLLocationCoordinate2DIsValid(coordinate) else{throw AppError.message("Coordinate non valide")}
        location=coordinate;let values=[coordinate.latitude,coordinate.longitude],key="gymCoordinate.\(engine.settings.gym.id)";savedCoordinates[engine.settings.gym.id]=values
        store.enqueue({try $0.save(values,key:key)},onError:{[weak self] _ in Task{@MainActor in self?.status="Posizione non salvata: riprova"}});monitor()
    }
    func monitor(){for region in manager.monitoredRegions where region.identifier.hasPrefix("homepulse.gym."){manager.stopMonitoring(for:region)}
        guard engine.settings.enabled,let location,CLLocationManager.isMonitoringAvailable(for:CLCircularRegion.self),manager.authorizationStatus == .authorizedAlways || manager.authorizationStatus == .authorizedWhenInUse else{return}
        let radius=min(manager.maximumRegionMonitoringDistance,max(50,min(1000,engine.settings.gym.radius_m)))
        let region=CLCircularRegion(center:location,radius:radius,identifier:"homepulse.gym.\(engine.settings.gym.id)");region.notifyOnEntry=true;region.notifyOnExit=true
        manager.startMonitoring(for:region);manager.requestState(for:region);status="Area palestra attiva"
    }
    var recent:[TimedBPM]=[]
    func accept(_ reading:Reading,manualWorkout:Bool){recent.append(TimedBPM(at:reading.date,bpm:reading.bpm));recent=recent.filter{reading.date.timeIntervalSince($0.at)<=120}
        let previous=engine.state
        let id=engine.ingest(TimedBPM(at:reading.date,bpm:reading.bpm),now:Date(),manualWorkout:manualWorkout)
        let state=engine.state;guard state != previous else{return}
        store.enqueue({[weak self] in try $0.save(state,key:"gymDetection")
            if let id{Task{@MainActor [weak self] in guard let self,self.engine.state.candidateSessionID==id else{return};self.onCandidate?(id,state.candidateAt ?? reading.date);self.notify(id)}}
        },onError:{[weak self] _ in Task{@MainActor in self?.status="Rilevamento sospeso: stato locale non salvato"}})
    }
    func resolve(){engine.resolve();let until=Date().addingTimeInterval(Double(engine.settings.cooldown_seconds));engine.state.cooldownUntil=max(until,engine.state.cooldownUntil ?? until);persist()}
    private func persist(){let state=engine.state;store.enqueue({try $0.save(state,key:"gymDetection")},onError:{[weak self] _ in Task{@MainActor in self?.status="Stato palestra non salvato"}})}
    func notify(_ id:UUID){let c=UNMutableNotificationContent();c.title="Home Pulse";c.body=Self.prompt;c.sound = .default;c.categoryIdentifier="gymCandidate";c.userInfo=["session_id":id.uuidString]
        center.add(UNNotificationRequest(identifier:"gym-\(id.uuidString)",content:c,trigger:nil))
    }
    func locationManagerDidChangeAuthorization(_ manager:CLLocationManager){updatePermissions();monitor()}
    func locationManager(_ manager:CLLocationManager,didEnterRegion region:CLRegion){guard region.identifier.hasPrefix("homepulse.gym.") else{return};enter()}
    func locationManager(_ manager:CLLocationManager,didExitRegion region:CLRegion){guard region.identifier.hasPrefix("homepulse.gym.") else{return};engine.exit();persist();onRegion?(false)}
    func locationManager(_ manager:CLLocationManager,didDetermineState state:CLRegionState,for region:CLRegion){guard region.identifier.hasPrefix("homepulse.gym.") else{return};if state == .inside{enter()}else if state == .outside{engine.exit();persist()}}
    private func enter(){let was=engine.state.inside;engine.enter(at:Date(),recent:recent);persist();if !was{onRegion?(true)}}
    func userNotificationCenter(_ center:UNUserNotificationCenter,willPresent notification:UNNotification)async->UNNotificationPresentationOptions{[.banner,.sound,.list]}
    func userNotificationCenter(_ center:UNUserNotificationCenter,didReceive response:UNNotificationResponse)async{
        guard let text=response.notification.request.content.userInfo["session_id"] as? String,let id=UUID(uuidString:text) else{return}
        if response.actionIdentifier=="notWorkout"{onReply?(id,nil,true)}
        else if let input=response as? UNTextInputNotificationResponse {onReply?(id,input.userText,false)}
    }
}
