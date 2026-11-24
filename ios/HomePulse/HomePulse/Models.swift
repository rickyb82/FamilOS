import Foundation

// Il contratto conserva anche campi server nuovi senza perdere i pacchetti originali.
enum JSONValue: Codable, Equatable {
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

struct BodyProfile: Codable, Equatable {
    var weight_kg: Double?; var body_fat_pct: Double?; var height_cm: Double?; var age: Int?
    var reported_lean_pct: Double?; var reported_retention_pct: Double?
    var goal: String?; var composition_source: String?; var resting_hr: Int?
    init() {}
    func validate() throws {
        for (value, low, high) in [(weight_kg,30.0,350.0),(body_fat_pct,2,65),(height_cm,100,250),
                                   (reported_lean_pct,2,98),(reported_retention_pct,0,100)] {
            if let value, !value.isFinite || value < low || value > high { throw AppError.message("Valore del profilo fuori intervallo") }
        }
        if (goal?.count ?? 0)>200 || (composition_source?.count ?? 0)>200{throw AppError.message("I testi del profilo possono avere al massimo 200 caratteri")}
        if let age, !(18...100).contains(age) { throw AppError.message("Età consentita: 18–100 anni") }
        if let resting_hr, !(20...200).contains(resting_hr) { throw AppError.message("FC a riposo non valida") }
    }
}
struct WorkoutPhase: Codable, Equatable, Identifiable {
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
struct WorkoutConfig: Codable, Equatable {
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
struct WeeklyRule: Codable, Equatable, Identifiable {
    var weekday:Int; var start:String; var end:String; var timezone:String="Europe/Rome"
    var id:String { "\(weekday)-\(start)-\(end)-\(timezone)" }
    static let defaults=[WeeklyRule(weekday:1,start:"13:30",end:"14:15"),WeeklyRule(weekday:3,start:"14:45",end:"15:30"),WeeklyRule(weekday:5,start:"08:45",end:"09:30")]
}
struct LocalSettings: Codable {
    var profile=BodyProfile(); var config=WorkoutConfig(); var weekly_schedule=WeeklyRule.defaults
    var auto_detection=AutoDetectionSettings()
    var revision=UUID().uuidString; var dirty=false
}
struct PhasePosition: Equatable {
    var name:String; var round:Int; var remaining:Int; var index:Int; var targetMin:Int?; var targetMax:Int?; var met:Double?; var complete:Bool
}
struct Energy: Equatable { var gross=0.0; var net=0.0; var estimatedSeconds=0.0; var unestimatedSeconds=0.0 }
struct WorkoutSession: Codable, Identifiable {
    var id:UUID; var activityName:String; var startedAt:Date; var endedAt:Date?
    var config:WorkoutConfig; var profile:BodyProfile
    var accumulated:Double=0; var runningSince:Date?; var state:String="running"
    var auto_detected=false;var provisional=false;var source:String="manual";var remoteID:String?
    func elapsed(at now:Date=Date())->Double { max(0,accumulated+(runningSince.map{max(0,now.timeIntervalSince($0))} ?? 0)) }
}
struct Reading: Identifiable, Codable { var id=UUID(); var date:Date; var bpm:Int }
struct StoredEvent: Codable, Identifiable {
    var event_id:String; var sequence:Int64; var captured_at:Double; var kind:String; var payload:JSONValue
    var id:String {event_id}
}
struct EventBatch: Codable { var batch_id:String; var events:[StoredEvent] }
struct Credentials: Codable { var device_id:String; var access_token:String; var server:URL }
struct RingBinding: Codable { var logical_ring_id:String; var owner_key_hex:String? }
enum AppError:LocalizedError { case message(String)
    var errorDescription:String? { if case .message(let s)=self{return s};return nil }
}

struct HistoricalMetrics {var temperatureC:Double?;var receivedAt:Date?;var motionSeconds:Int?;var steps:Int?}

// Identificativi verificati nel catalogo Android, non dedotti dal nome libero.
enum ActivityCatalog { static let defaults:[(Int,String)]=[(65535,"Altro"),(14,"Camminata"),(12,"Corsa"),(5,"Ciclismo"),(8,"Pesi"),(10,"Pilates"),(15,"Yoga")] }

enum RingChargeState:String {
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
struct RingBatterySnapshot:Codable {var level:Int;var at:Date;var chargingProgressRaw:Int?=nil}
/// One alert per discharge. Hysteresis prevents repeated alerts around 10%.
struct BatteryAlertState:Codable {
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
struct BatteryFullAlertState:Codable {
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
