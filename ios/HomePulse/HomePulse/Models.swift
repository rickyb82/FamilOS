import Foundation
import Darwin

enum ProtectedFileIO {
    /// Derived upload files and technical snapshots can be rebuilt. Replace
    /// them atomically in their own directory, without Foundation's replacement
    /// directory search. The durable sensor journal remains separate.
    static func replace(_ data:Data,at url:URL)throws{
        let pending=url.appendingPathExtension("pending")
        let descriptor=pending.withUnsafeFileSystemRepresentation{path in
            path.map{Darwin.open($0,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0o600)} ?? -1
        }
        guard descriptor>=0 else{throw CocoaError(.fileWriteUnknown)}
        var renamed=false
        defer{Darwin.close(descriptor);if !renamed{_ = pending.withUnsafeFileSystemRepresentation{path in path.map{Darwin.unlink($0)} ?? -1}}}
        try data.withUnsafeBytes{bytes in
            var offset=0
            while offset<bytes.count{
                let count=Darwin.write(descriptor,bytes.baseAddress!.advanced(by:offset),bytes.count-offset)
                if count<0 && errno==EINTR{continue}
                guard count>0 else{throw CocoaError(.fileWriteUnknown)};offset+=count
            }
        }
        try FileManager.default.setAttributes([.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication],ofItemAtPath:pending.path)
        let result=pending.withUnsafeFileSystemRepresentation{source in url.withUnsafeFileSystemRepresentation{destination in
            guard let source,let destination else{return Int32(-1)}
            return Darwin.rename(source,destination)
        }}
        guard result==0 else{throw CocoaError(.fileWriteUnknown)};renamed=true
    }
}

/// Best-effort technical snapshots must never wait for storage on the UI actor.
/// While a write is pending, retain only the most recent snapshot.
final class DiagnosticWriter:@unchecked Sendable {
    private let url:URL;private let minimumInterval:TimeInterval
    private let queue=DispatchQueue(label:"HomePulse.diagnostic-writer",qos:.utility)
    private let lock=NSLock();private var pending:JSONValue?;private var scheduled=false;private var nextWriteAt=0.0
    private let writeFile:@Sendable(Data,URL)throws->Void
    init(url:URL,minimumInterval:TimeInterval=5,writeFile:(@Sendable(Data,URL)throws->Void)?=nil){
        self.url=url;self.minimumInterval=minimumInterval
        self.writeFile=writeFile ?? {data,url in try ProtectedFileIO.replace(data,at:url)}
    }
    func submit(_ value:JSONValue){
        lock.lock();pending=value
        guard !scheduled else{lock.unlock();return}
        scheduled=true;let delay=max(0,nextWriteAt-ProcessInfo.processInfo.systemUptime);lock.unlock()
        queue.asyncAfter(deadline:.now()+delay){self.flush()}
    }
    private func flush(){
        lock.lock();let value=pending;pending=nil;lock.unlock()
        if let value,let data=try? JSONEncoder().encode(value){try? writeFile(data,url)}
        lock.lock();nextWriteAt=ProcessInfo.processInfo.systemUptime+minimumInterval
        let again=pending != nil;if !again{scheduled=false};lock.unlock()
        if again{queue.asyncAfter(deadline:.now()+minimumInterval){self.flush()}}
    }
}

// Il contratto conserva anche campi server nuovi senza perdere i pacchetti originali.
enum JSONValue: Codable, Equatable, Sendable {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try c.decode([JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    subscript(_ key: String) -> JSONValue { if case .object(let v) = self { return v[key] ?? .null }; return .null }
    var string: String? { if case .string(let v) = self { return v }; return nil }
    var number: Double? { if case .number(let v) = self { return v }; return nil }
    var array: [JSONValue] { if case .array(let v) = self { return v }; return [] }
    var object: [String: JSONValue] { if case .object(let v) = self { return v }; return [:] }
    static func encoded<T: Encodable>(_ value: T) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(value))
    }
}

struct BodyProfile: Codable, Equatable, Sendable {
    var weight_kg: Double?; var body_fat_pct: Double?; var height_cm: Double?; var age: Int?
    var reported_lean_pct: Double?; var reported_retention_pct: Double?
    var goal: String?; var composition_source: String?; var resting_hr: Int?; var energy_sex:String?
    init() {}
    func validate() throws {
        if let energy_sex, !["male","female"].contains(energy_sex){throw AppError.message("Parametro energetico non valido")}
        for (value, low, high) in [(weight_kg,30.0,350.0),(body_fat_pct,2,65),(height_cm,100,250),
                                   (reported_lean_pct,2,98),(reported_retention_pct,0,100)] {
            if let value, !value.isFinite || value < low || value > high { throw AppError.message("Valore del profilo fuori intervallo") }
        }
        if (goal?.count ?? 0)>200 || (composition_source?.count ?? 0)>200{throw AppError.message("I testi del profilo possono avere al massimo 200 caratteri")}
        if let age, !(18...100).contains(age) { throw AppError.message("Età consentita: 18–100 anni") }
        if let resting_hr, !(20...200).contains(resting_hr) { throw AppError.message("FC a riposo non valida") }
    }
}
struct WorkoutPhase: Codable, Equatable, Identifiable, Sendable {
    var id: UUID = UUID(); var name: String; var duration: Int
    var target_min: Int?; var target_max: Int?; var met: Double?
    enum CodingKeys: String, CodingKey { case id, name, duration, target_min, target_max, met }
    init(name: String = "Fase", duration: Int = 60, target_min: Int? = nil, target_max: Int? = nil, met: Double? = nil) {
        self.name=name; self.duration=duration; self.target_min=target_min; self.target_max=target_max; self.met=met
    }
    init(from decoder: Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        id=(try? c.decode(UUID.self,forKey:.id)) ?? UUID()
        name=(try? c.decode(String.self,forKey:.name)) ?? "Fase"
        duration=(try? c.decode(Int.self,forKey:.duration)) ?? 60
        target_min=try? c.decode(Int.self,forKey:.target_min); target_max=try? c.decode(Int.self,forKey:.target_max)
        met=try? c.decode(Double.self,forKey:.met)
    }
}
struct WorkoutConfig: Codable, Equatable, Sendable {
    var work=40; var rest=20; var rounds=8; var plan:[WorkoutPhase]=[]
    var activity_name:String?;var activity_id:Int?;var hrmax:Int?
    enum CodingKeys:String,CodingKey { case work,rest,rounds,plan,activity_name,activity_id,hrmax }
    init() {}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        work=(try? c.decode(Int.self,forKey:.work)) ?? 40
        rest=(try? c.decode(Int.self,forKey:.rest)) ?? 20
        rounds=(try? c.decode(Int.self,forKey:.rounds)) ?? 8
        activity_name=try? c.decode(String.self,forKey:.activity_name);activity_id=try? c.decode(Int.self,forKey:.activity_id);hrmax=try? c.decode(Int.self,forKey:.hrmax)
        plan=(try? c.decode([WorkoutPhase].self,forKey:.plan)) ?? []
    }
    var phases:[WorkoutPhase] {
        if !plan.isEmpty { return plan }
        var p=[WorkoutPhase(name:"Lavoro",duration:work)]
        if rest>0 { p.append(WorkoutPhase(name:"Recupero",duration:rest)) }; return p
    }
    var total:Double { Double(phases.reduce(0){$0+$1.duration} * rounds) }
    func validate() throws {
        guard (5...3600).contains(work), (0...3600).contains(rest), (1...100).contains(rounds), plan.count<=40 else {
            throw AppError.message("Durate, ripetizioni o numero di fasi non validi")
        }
        for p in phases {
            guard !p.name.trimmingCharacters(in:.whitespaces).isEmpty,p.name.count<=80,(5...7200).contains(p.duration) else { throw AppError.message("Controlla nome e durata delle fasi") }
            guard (p.target_min == nil) == (p.target_max == nil) else { throw AppError.message("Inserisci entrambi gli estremi FC") }
            if let a=p.target_min,let b=p.target_max, !(40...240).contains(a) || !(40...240).contains(b) || a>=b { throw AppError.message("Intervallo FC non valido") }
            if let met=p.met, !met.isFinite || !(1...20).contains(met) { throw AppError.message("MET consentiti: 1–20") }
        }
        if (activity_name?.count ?? 0)>80{throw AppError.message("Nome attività: massimo 80 caratteri")}
        if let hrmax, !(80...240).contains(hrmax){throw AppError.message("FC massima fuori intervallo")}
        guard total<=86400 else { throw AppError.message("La sessione non può superare 24 ore") }
    }
}
struct WeeklyRule: Codable, Equatable, Identifiable, Sendable {
    var weekday:Int; var start:String; var end:String; var timezone:String="Europe/Rome"
    var id:String { "\(weekday)-\(start)-\(end)-\(timezone)" }
    static let defaults:[WeeklyRule]=[]
}
struct LocalSettings: Codable, Sendable {
    var profile=BodyProfile(); var config=WorkoutConfig(); var weekly_schedule=WeeklyRule.defaults
    var auto_detection=AutoDetectionSettings()
    var revision=UUID().uuidString; var dirty=false
}
struct PhasePosition: Equatable {
    var name:String; var round:Int; var remaining:Int; var index:Int; var targetMin:Int?; var targetMax:Int?; var met:Double?; var complete:Bool
}
struct Energy: Equatable { var gross=0.0; var net=0.0; var estimatedSeconds=0.0; var unestimatedSeconds=0.0 }
struct WorkoutSession: Codable, Identifiable, Sendable {
    var id:UUID; var activityName:String; var startedAt:Date; var endedAt:Date?
    var config:WorkoutConfig; var profile:BodyProfile
    var accumulated:Double=0; var runningSince:Date?; var state:String="running"
    var auto_detected=false;var provisional=false;var source:String="manual";var remoteID:String?
    func elapsed(at now:Date=Date())->Double { max(0,accumulated+(runningSince.map{max(0,now.timeIntervalSince($0))} ?? 0)) }
}
enum DiaryRange:Int,CaseIterable,Identifiable,Sendable {
    case two=2,four=4,eight=8,day=24,twoDays=48,week=168
    var id:Int{rawValue}
    var label:String{switch self{case .twoDays:return "2 giorni";case .week:return "7 giorni";default:return "\(rawValue) ore"}}
}
struct DiaryCleanup:Codable,Equatable,Sendable {
    var retain_start:Double;var retain_end:Double;var created_before:Double
    var valid:Bool{retain_start.isFinite && retain_end.isFinite && created_before.isFinite && retain_start>0 && retain_start<retain_end && retain_end<=created_before}
    func allows(_ session:WorkoutSession)->Bool{
        let start=session.startedAt.timeIntervalSince1970
        let end=session.endedAt?.timeIntervalSince1970 ?? start+session.accumulated
        return start>created_before || (start<=retain_end && end>=retain_start)
    }
}
struct Reading: Identifiable, Codable, Sendable { var id=UUID(); var date:Date; var bpm:Int; var segment:Int? }
struct StoredEvent: Codable, Identifiable, Sendable {
    var event_id:String; var sequence:Int64; var captured_at:Double; var kind:String; var payload:JSONValue
    var id:String {event_id}
}
struct EventBatch: Codable, Sendable { var batch_id:String; var events:[StoredEvent] }
struct Credentials: Codable, Sendable { var device_id:String; var access_token:String; var server:URL }
struct RingBinding: Codable, Sendable { var logical_ring_id:String; var owner_key_hex:String? }
enum AppError:LocalizedError { case message(String)
    var errorDescription:String? { if case .message(let s)=self{return s};return nil }
}

struct HistoricalMetrics:Sendable {var temperatureC:Double?;var receivedAt:Date?;var motionSeconds:Int?;var steps:Int?}

struct SleepPeriod:Identifiable,Sendable {
    var id:String;var start:Date;var end:Date;var zoneMinutes:Int;var timeInBed:Double
    var total:Double?;var stages:[String:Double];var latency:Double?;var efficiency:Double?
    var phases:String?;var phaseStep:Int;var spo2:JSONValue;var breathing:JSONValue;var displayZone:String?;var firmwareBedtime:Bool
    init?(_ value:JSONValue){
        guard let id=value["id"].string,let start=value["started_at"].number,let end=value["ended_at"].number,start.isFinite,end.isFinite,end>start,
              let time=value["time_in_bed_seconds"].number,time.isFinite,time>=0 else{return nil}
        self.id=id;self.start=Date(timeIntervalSince1970:start);self.end=Date(timeIntervalSince1970:end)
        zoneMinutes=Int(value["time_zone_minutes"].number ?? 0);timeInBed=time
        total=value["total_sleep_seconds"].number;latency=value["latency_seconds"].number;efficiency=value["efficiency"].number
        stages=value["stage_seconds"].object.compactMapValues{$0.number};spo2=value["spo2"];breathing=value["respiratory_rate"];displayZone=value["display_time_zone"].string;firmwareBedtime=value["source"].string=="oura-ring-bedtime"
        if let raw=value["phases_30_seconds"].string,!raw.isEmpty{phases=raw;phaseStep=30}
        else if let raw=value["phases_5_minutes"].string,!raw.isEmpty{phases=raw;phaseStep=300}
        else{phases=nil;phaseStep=0}
    }
    var phaseRuns:[SleepPhaseRun]{
        guard let phases else{return []}
        var result:[SleepPhaseRun]=[]
        for (index,phase) in phases.enumerated(){
            guard let stage=[Character("1"):"Profondo","2":"Leggero","3":"REM","4":"Sveglio"][phase] else{continue}
            let start=index*phaseStep,end=start+phaseStep
            if let last=result.last,last.stage==stage,last.end==start{result[result.count-1].end=end}
            else{result.append(SleepPhaseRun(start:start,end:end,stage:stage))}
        }
        return result
    }
    func clock(_ date:Date)->String{
        let formatter=DateFormatter();formatter.locale=Locale(identifier:"it_IT");formatter.timeZone=displayZone.flatMap{TimeZone(identifier:$0)} ?? TimeZone(secondsFromGMT:zoneMinutes*60);formatter.dateFormat="d MMM · HH:mm";return formatter.string(from:date)
    }
}
struct SleepPhaseRun:Identifiable,Sendable{var start:Int;var end:Int;var stage:String;var id:Int{start}}

// Identificativi verificati nel catalogo Android, non dedotti dal nome libero.
enum ActivityCatalog { static let defaults:[(Int,String)]=[(65535,"Altro"),(14,"Camminata"),(12,"Corsa"),(5,"Ciclismo"),(8,"Pesi"),(10,"Pilates"),(15,"Yoga")] }

enum RingChargeState:String,Sendable {
    case idle,charging,full
    init?(progress:Int?){
        guard let progress,(0...100).contains(progress) else{return nil}
        self=progress==0 ? .idle:(progress==100 ? .full:.charging)
    }
    var label:String{switch self{case .idle:return "Non in carica";case .charging:return "In carica";case .full:return "Carica completa"}}
    var isCharging:Bool{self != .idle}
}
enum ChargeCheckPolicy {
    static func delay(foreground:Bool,desired:Bool,ready:Bool,busy:Bool,progress:Int?)->TimeInterval?{
        guard !foreground,desired,ready,!busy,RingChargeState(progress:progress)?.isCharging == true else{return nil}
        return 60
    }
}
enum RingReconnectPolicy {
    static func delay(consecutiveFailures:Int)->TimeInterval{
        [5.0,10,20,30,60][max(1,min(5,consecutiveFailures))-1]
    }
}
struct RingBatterySnapshot:Codable, Sendable {var level:Int;var at:Date;var chargingProgressRaw:Int?=nil}
/// One alert per discharge. Hysteresis prevents repeated alerts around 10%.
struct BatteryAlertState:Codable, Sendable {
    var armed=true
    mutating func observe(_ level:Int)->Bool {
        guard (0...100).contains(level) else{return false}
        if level>=20{armed=true;return false}
        guard level<=10,armed else{return false}
        armed=false;return true
    }
}


/// One completion alert until the ring is observed off the charger, or in a
/// later charging cycle below 90%. A brief 99/100 fluctuation stays latched.
struct BatteryFullAlertState:Codable, Sendable {
    var armed=true
    mutating func observe(level:Int,progress:Int?)->Bool {
        guard (0...100).contains(level),let state=RingChargeState(progress:progress) else{return false}
        if state == .idle || state == .charging && level<=90{armed=true;return false}
        guard state == .full,armed else{return false}
        armed=false;return true
    }
}


/// Retains an actual observation for display; invalid packets never renew its timestamp.
struct HeartDisplayState {
    private(set) var bpm:Int?
    private(set) var capturedAt:Date?
    private(set) var latestPacketValid=false
    private(set) var flags:Int?
    private(set) var ibiValidity:Int?
    private(set) var measurementState:Int?
    mutating func receive(_ value:OuraLive,at date:Date)->Bool {
        flags=value.flags;ibiValidity=value.validity;measurementState=value.state
        guard let next=value.bpm,OuraWire.displaySupported(next) else{latestPacketValid=false;return false}
        bpm=next;capturedAt=date;latestPacketValid=true;return true
    }
    func isFresh(at date:Date)->Bool {
        guard latestPacketValid,let capturedAt else{return false}
        let age=date.timeIntervalSince(capturedAt);return age>=0 && age<=8
    }
    func ageSeconds(at date:Date)->Int? {capturedAt.map{max(0,Int(date.timeIntervalSince($0)))}}
}

/// A display-only rolling mean. Missing or repeated timestamps never count.
struct FiveReadingAverage {
    private var window:[Reading]=[]
    mutating func accept(_ reading:Reading)->Int? {
        if let last=window.last {
            guard reading.date>last.date else{return nil}
            if reading.date.timeIntervalSince(last.date)>8{window=[]}
        }
        window.append(reading);if window.count>5{window.removeFirst()}
        guard window.count==5 else{return nil}
        return Int((Double(window.reduce(0){$0+$1.bpm})/5).rounded())
    }
    static func series(_ readings:[Reading])->[Reading]{
        var average=Self()
        return readings.compactMap{p in average.accept(p).map{Reading(id:p.id,date:p.date,bpm:$0)}}
    }
}

enum ContinuousCardioPolicy {
    static let segmentSeconds:TimeInterval=3600
    static func needsRotation(_ result:RecordedResult?,at date:Date)->Bool{
        guard let result,!result.final,result.availableUntil-result.startedAt>=segmentSeconds else{return false}
        // Do not restart on old data or treat absence of HR as removal.
        let age=date.timeIntervalSince1970-result.availableUntil
        return age>=0 && age<90 && result.latest != nil
    }
}
