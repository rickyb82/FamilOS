import Foundation
import CommonCrypto

struct OuraLive:Sendable {
    var bpm:Int?;var ibi:Int?;var flags:Int;var state:Int;var validity:Int;var age:Int;var temperatureRaw:Int;var warnings:[String]
}

struct PacketJournalDelivery:Sendable {
    var frames:[Data];var at:Date;var error:String?;var storageFailed:Bool
}
final class PacketJournal:@unchecked Sendable {
    private let queue=DispatchQueue(label:"HomePulse.bluetooth-archive",qos:.userInitiated)
    private var framer=OuraFramer();private let store:EventStore
    init(store:EventStore){self.store=store}
    func reset(){queue.async{self.framer=OuraFramer()}}
    func ingest(_ value:Data,at:Date,api:[Int],ring:String?,completion:@escaping @Sendable(PacketJournalDelivery)->Void){
        queue.async{
            var frames:[Data]=[],failure:String?
            do{_ = try self.framer.consume(value){frames.append($0)}}catch{failure=error.localizedDescription}
            do{try self.store.ingestPackets(frames,api:api,ring:ring,at:at,opaque:self.framer.opaqueRejected)}
            catch{completion(PacketJournalDelivery(frames:[],at:at,error:"Archivio non scrivibile: acquisizione fermata",storageFailed:true));return}
            completion(PacketJournalDelivery(frames:frames,at:at,error:failure,storageFailed:false))
        }
    }
}
struct OuraBattery:Equatable {
    var level:Int
    var chargingProgressRaw:Int
    var chargingRecommendationRaw:Int
    var isCharging:Bool?{RingChargeState(progress:chargingProgressRaw)?.isCharging}
}
struct OuraFeatureStatus:Equatable,Sendable{var mode:Int;var flags:Int;var state:Int;var result:Int}
struct OuraExerciseLatest:Sendable{
    var result:Int;var flags:Int?;var state:Int?;var ageRaw:Int?;var qualityRaw:Int?;var lastHrRaw:Int?
}
enum ExercisePolling{
    static func nextDeadline(previous:Date,now:Date,due:Bool,manualControlPending:Bool)->Date{
        due ? now.addingTimeInterval(manualControlPending ? 5:60):previous
    }
}
struct RecordedWorkoutCapture{
    var phase="baseline";var baselineComplete=false;var requestedAt:Date?;var stopRequestedAt:Date?;var stoppedAt:Date?
    var baselineNextRingTime:UInt32?;var firstTraceAt:Date?;var firstTraceRingTime:UInt32?
    var sessionStartRingTime:UInt32?;var lastSessionStatus:Int?;var recordsAfterSessionStart=0
    var pages=0;var lastPageAt:Date?;var bytesLeft:UInt32?;var counts:[String:Int]=[:]
    var firstTraceAfterStopAt:Date?;var firstTraceAfterStopRingTime:UInt32?
    var traceTypes:[String:Int]=[:]
    var countComplete=true;private var seen=Set<Data>()
    mutating func note(_ frame:Data,at:Date){
        guard let rt=try? OuraWire.cursor(frame),!seen.contains(frame) else{return}
        guard seen.count<5000 else{countComplete=false;return};seen.insert(frame)
        let bytes=[UInt8](frame),key=phase+"_"+String(format:"%02x",bytes[0]);counts[key,default:0]+=1
        if bytes[0]==0x73,bytes.count>=8{traceTypes[phase+"_"+String(bytes[7]),default:0]+=1}
        if phase != "baseline",bytes[0]==0x6c,bytes.count>=8,bytes[6]==3{
            lastSessionStatus=Int(bytes[7]);if bytes[7]<=1{sessionStartRingTime=rt}
        }
        if phase=="recording",bytes[0]==0x73{
            if firstTraceAt==nil{firstTraceAt=at;firstTraceRingTime=rt}
        }
        if phase=="after_stop",bytes[0]==0x73,firstTraceAfterStopAt==nil{firstTraceAfterStopAt=at;firstTraceAfterStopRingTime=rt}
        if phase != "baseline",bytes[0]==0x73,let start=sessionStartRingTime,rt>=start{recordsAfterSessionStart+=1}
    }
    var diagnostic:JSONValue{
        var value:[String:JSONValue]=["phase":.string(phase),"baseline_complete":.bool(baselineComplete),"counts_complete":.bool(countComplete),"pages":.number(Double(pages)),"records":.object(counts.mapValues{.number(Double($0))}),"trace_types_raw":.object(traceTypes.mapValues{.number(Double($0))}),"ring_time_tick_ms":.number(100),"ehr_after_observed_start":.number(Double(recordsAfterSessionStart))]
        if let requestedAt{value["requested_at"] = .number(requestedAt.timeIntervalSince1970)}
        if let stopRequestedAt{value["stop_requested_at"] = .number(stopRequestedAt.timeIntervalSince1970)}
        if let stoppedAt{value["stopped_at"] = .number(stoppedAt.timeIntervalSince1970)}
        if let baselineNextRingTime{value["baseline_next_ring_time"] = .number(Double(baselineNextRingTime))}
        if let firstTraceAt{value["first_trace_received_at"] = .number(firstTraceAt.timeIntervalSince1970)}
        if let firstTraceRingTime{value["first_trace_ring_time_raw"] = .number(Double(firstTraceRingTime))}
        if let firstTraceAfterStopAt{value["first_trace_after_stop_received_at"] = .number(firstTraceAfterStopAt.timeIntervalSince1970)}
        if let firstTraceAfterStopRingTime{value["first_trace_after_stop_ring_time_raw"] = .number(Double(firstTraceAfterStopRingTime))}
        if let sessionStartRingTime{value["session_start_ring_time_raw"] = .number(Double(sessionStartRingTime))}
        if let lastSessionStatus{value["last_session_status_raw"] = .number(Double(lastSessionStatus))}
        if let lastPageAt{value["last_page_at"] = .number(lastPageAt.timeIntervalSince1970)}
        if let bytesLeft{value["last_bytes_left"] = .number(Double(bytesLeft))}
        return .object(value)
    }
    var recordingTraceCount:Int{counts["recording_73",default:0]}
    var recordingAcmCount:Int{counts["recording_74",default:0]}
}
struct OuraCommandRejection:LocalizedError,Equatable {
    let feature:UInt8;let command:UInt8;let result:UInt8;let operation:String?
    var errorDescription:String?{
        let prefix=operation.map{$0+": "} ?? ""
        return prefix+"Comando anello rifiutato (esito \(result))"
    }
}
enum OuraWire {
    static let service="98ed0001-a541-11e4-b6a0-0002a5d5c51b"
    static let write="98ed0002-a541-11e4-b6a0-0002a5d5c51b", notify="98ed0003-a541-11e4-b6a0-0002a5d5c51b"
    static func complete(_ data:Data,minimum:Int,tag:UInt8,extended:UInt8?=nil)throws->[UInt8]{
        let b=[UInt8](data);guard b.count>=minimum,b[0]==tag,Int(b[1])+2==b.count else{throw AppError.message("Pacchetto anello incompleto")}
        if let extended, b.count<3 || b[2] != extended {throw AppError.message("Risposta anello inattesa")};return b
    }
    // GetBatteryLevel: first three payload bytes are level, charge progress and
    // recommendation. Gen3 can append fields; keep the complete frame in the journal.
    static func battery(_ frame:Data)throws->OuraBattery{
        let b=try complete(frame,minimum:5,tag:0x0d)
        guard (0...100).contains(Int(b[2])) else{throw AppError.message("Livello batteria anello non valido")}
        return OuraBattery(level:Int(b[2]),chargingProgressRaw:Int(b[3]),chargingRecommendationRaw:Int(b[4]))
    }
    static func firmware(_ frame:Data)throws->[Int]{let b=try complete(frame,minimum:20,tag:9);return b[2..<5].map(Int.init)}
    static func capabilities(_ frame:Data)throws->(Int,[Int:Int]){
        let b=try complete(frame,minimum:4,tag:0x2f,extended:2)
        guard (1...16).contains(Int(b[3])),(b.count-4)%2==0 else{throw AppError.message("Capability anello non valide")}
        var v:[Int:Int]=[:];for i in stride(from:4,to:b.count,by:2){if let old=v[Int(b[i])],old != Int(b[i+1]){throw AppError.message("Capability incoerenti")};v[Int(b[i])]=Int(b[i+1])};return(Int(b[3]),v)
    }
    static func ack(_ frame:Data,extended:UInt8,feature:UInt8,operation:String?=nil)throws {
        let b=try complete(frame,minimum:5,tag:0x2f,extended:extended)
        guard b[3]==feature else{throw AppError.message("Conferma anello riferita a una funzione diversa")}
        guard b[4]==0 else{throw OuraCommandRejection(feature:feature,command:extended,result:b[4],operation:operation)}
    }
    static func featureStatus(_ frame:Data,feature:UInt8)throws->OuraFeatureStatus{
        let bytes=try complete(frame,minimum:8,tag:0x2f,extended:0x21)
        guard bytes[3]==feature,(0...3).contains(bytes[4]),(0...3).contains(bytes[6]) else{throw AppError.message("Stato della funzione anello non riconosciuto")}
        return OuraFeatureStatus(mode:Int(bytes[4]),flags:Int(bytes[5]),state:Int(bytes[6]),result:Int(bytes[7]))
    }
    static func modeChangeNeeded(_ frame:Data,feature:UInt8,desired:UInt8)throws->Bool{
        let bytes=try complete(frame,minimum:8,tag:0x2f,extended:0x21)
        guard bytes[3]==feature,(0...3).contains(bytes[4]),(0...3).contains(desired) else{throw AppError.message("Modalità funzione anello non riconosciuta")}
        // Original setter compares mode only. Status result is not a query ACK.
        return bytes[4] != desired
    }
    static func exerciseLatest(_ frame:Data)throws->OuraExerciseLatest{
        let bytes=try complete(frame,minimum:5,tag:0x2f,extended:0x25)
        guard bytes[3]==3 else{throw AppError.message("Risposta cardio attività inattesa")}
        if bytes.count==5,bytes[4] != 0{return OuraExerciseLatest(result:Int(bytes[4]))}
        guard bytes.count>=17 else{throw AppError.message("Valori cardio attività incompleti")}
        return OuraExerciseLatest(result:Int(bytes[4]),flags:Int(bytes[5]),state:Int(bytes[6]),ageRaw:Int(bytes[7])|(Int(bytes[8])<<8),qualityRaw:Int(bytes[9]),lastHrRaw:Int(bytes[13]))
    }
    static func spo2AutomaticParameters(capabilityVersion:Int)->Data?{
        // Original HealthSpo2 defaults, eight-parameter ABI advertised by Gen3.
        // Version3 adds HFM duration; retain firmware parameters until verified.
        guard (1...2).contains(capabilityVersion) else{return nil}
        return Data([0x2f,0x0a,0x29,4,1,100,3,30,24,0,0,0])
    }
    static func cipher(key:Data,nonce:Data)throws->Data {
        guard key.count==16,nonce.count==15 else{throw AppError.message("Credenziale anello non disponibile")}
        var result=Data(count:32);var length=0
        let status=result.withUnsafeMutableBytes{out in key.withUnsafeBytes{k in nonce.withUnsafeBytes{n in
            CCCrypt(CCOperation(kCCEncrypt),CCAlgorithm(kCCAlgorithmAES),CCOptions(kCCOptionECBMode|kCCOptionPKCS7Padding),k.baseAddress,key.count,nil,n.baseAddress,nonce.count,out.baseAddress,32,&length)
        }}}
        guard status==kCCSuccess,length==16 else{throw AppError.message("Autenticazione anello non riuscita")};result.count=length;return result
    }
    static func displaySupported(_ bpm:Int)->Bool{(20...250).contains(bpm)}
    static func live(_ frame:Data,api:[Int])throws->OuraLive {
        let modern=api.lexicographicallyPrecedes([1,10,0]) == false
        let b=try complete(frame,minimum:modern ? 17:15,tag:0x2f,extended:0x28)
        guard b[3]==2 else{throw AppError.message("Notifica diversa dal battito live")}
        let ibi=Int(b[8])|((Int(b[9])&15)<<8),validity=Int(b[9]>>4)
        let valid=b[5]==2 && b[4]&1 != 0 && validity==1 && ibi>0 && ibi<=2000
        let names:[(UInt8,String)]=[(16,"Movimento segnalato"),(4,"Segnale ottico debole"),(8,"Dita fredde"),(2,"Ricerca segnale"),(32,"Identificazione segnale")]
        let offset=modern ? 14:13
        return OuraLive(bpm:valid ? Int((60000.0/Double(ibi)).rounded()):nil,ibi:valid ? ibi:nil,flags:Int(b[4]),state:Int(b[5]),validity:validity,age:Int(b[6])|(Int(b[7])<<8),temperatureRaw:Int(Int16(bitPattern:UInt16(b[offset])|(UInt16(b[offset+1])<<8))),warnings:names.filter{b[4]&$0.0 != 0}.map{$0.1})
    }
    static func sensitive(_ data:Data)->Bool {
        let b=[UInt8](data);guard !b.isEmpty else{return false}
        if b[0]==0x24 || b[0]==0x25{return true}
        return b.count>=3 && b[0]==0x2f && (0x2b...0x2f).contains(b[2])
    }
    static func historyRequest(cursor:UInt32,max:UInt8=64)->Data {Data([0x10,9,UInt8(cursor&255),UInt8((cursor>>8)&255),UInt8((cursor>>16)&255),UInt8((cursor>>24)&255),max,255,255,255,255])}
    static func cursor(_ frame:Data)throws->UInt32 {let b=[UInt8](frame);guard b.count>=6,b[0]>=0x41,b[0]<=0x85,Int(b[1])+2==b.count else{throw AppError.message("Cursore storico esteso non supportato")};return UInt32(b[2])|(UInt32(b[3])<<8)|(UInt32(b[4])<<16)|(UInt32(b[5])<<24)}
    static func sportParameters(activity:UInt16,age:Int,resting:Int,capabilityVersion:Int=0)throws->Data {
        guard (18...100).contains(age),(30...200).contains(resting) else{throw AppError.message("Servono età e FC a riposo valide")}
        let wireActivity:UInt16=capabilityVersion>0 ? 0:activity
        return Data([0x2f,7,0x29,3,UInt8(wireActivity&255),UInt8(wireActivity>>8),UInt8(resting),50,UInt8(230-age)])
    }
}
struct OuraFramer {
    private(set) var buffer=Data()
    private(set) var opaqueRejected:Data?
    mutating func consume(_ value:Data,onFrame:((Data)throws->Void)?=nil)throws->[Data] {
        opaqueRejected=nil
        buffer.append(value);guard buffer.count<=65536 else{buffer.removeAll();throw AppError.message("Buffer anello oltre limite")}
        var frames:[Data]=[]
        while buffer.count>=2 {
            let b=[UInt8](buffer),length:Int
            if b[0]==0xfe {opaqueRejected=buffer;buffer.removeAll();throw AppError.message("Formato storico FE conservato raw ma non decodificato")}
            if b[0]==0xfd {guard b.count>=4 else{break};length=Int(b[3])+4}else{length=Int(b[1])+2}
            guard length>=2,length<=4096 else{buffer.removeAll();throw AppError.message("Lunghezza anello inattesa")}
            guard buffer.count>=length else{break};let frame=Data(buffer.prefix(length));buffer.removeFirst(length);try onFrame?(frame);frames.append(frame)
        }
        return frames
    }
}
