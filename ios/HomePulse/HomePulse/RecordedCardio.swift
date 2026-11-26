import Foundation
import Combine
import CryptoKit
import UIKit

struct RecordedContext:Codable,Sendable {
    var token=UUID().uuidString
    var ring:String;var afterRow:Int64;var version:Int;var activity:Int
    var profile:BodyProfile;var workoutID:String?;var revision=0
}
struct RecordedRaw:Sendable {var row:Int64;var data:Data}
struct RecordedPoint:Codable,Sendable {
    var offset:Double;var bpm:Double?;var quality:Double?
}
struct RecordedResult:Codable,Sendable {
    var token:String;var revision:Int;var inputDigest:String;var workoutID:String?
    var startedAt:Double;var availableUntil:Double;var endedAt:Double?
    var signalCheck:Int;var points:[RecordedPoint];var ppgWindows:Int;var accelWindows:Int
    var final:Bool;var model="WHR-U2.5.1";var source="oura-recorded-offline"
    var latest:RecordedPoint? {
        guard signalCheck != -2 else{return nil}
        return points.last{p in p.offset>=0 && startedAt+p.offset<=availableUntil &&
            (p.bpm.map{(20...250).contains($0)} ?? false) && p.quality != nil}
    }
    var acceptedReadings:[Reading] {
        guard signalCheck != -2 else{return []}
        return points.compactMap{p in
            guard let bpm=p.bpm,(20...250).contains(bpm),p.quality != nil,p.offset>=0,startedAt+p.offset<=availableUntil else{return nil}
            return Reading(date:Date(timeIntervalSince1970:startedAt+p.offset),bpm:Int(bpm))
        }
    }
    var payload:JSONValue {
        var p=(try? JSONValue.encoded(self).object) ?? [:]
        p["interpolated"] = .bool(true);p["experimental"] = .bool(true)
        p["time_source"] = .string("ring-timesync-100ms")
        p["resting_hr_source"] = .string("profile_resting_hr")
        p["sex_input"] = .string("unspecified_zero")
        p["demographics_clamped_by_model"] = .bool(true)
        return .object(p)
    }
}
struct RecordedTensors:Sendable {
    var ppg:[Float];var accel:[Float];var intensity:[Float];var timeline:[Float]
    var activity:Int;var demographics:[Float]
    var startedAt:Double;var availableUntil:Double;var endedAt:Double?
    var digest:String
}

enum RecordedBuilder {
    static func canReleaseInactive(_ records:[RecordedRaw],hasResult:Bool)->Bool{
        guard !hasResult else{return false}
        let hasTrace=records.contains{$0.data.first==0x73}
        let hasEnd=records.contains{let b=[UInt8]($0.data);return b.count>=8 && b[0]==0x6c && b[6]==3 && (2...11).contains(b[7])}
        return !hasTrace || hasEnd
    }
    static func tick(_ b:[UInt8])->UInt32? {
        guard b.count>=6,Int(b[1])+2==b.count else{return nil}
        return UInt32(b[2])|UInt32(b[3])<<8|UInt32(b[4])<<16|UInt32(b[5])<<24
    }
    static func anchor(_ data:Data)->(UInt32,Double)? {
        let b=[UInt8](data)
        guard b.count>=14,b[0]==0x42,let rt=tick(b) else{return nil}
        let seconds=(0..<8).reduce(UInt64(0)){$0|UInt64(b[6+$1])<<($1*8)}
        guard seconds>=1_262_304_000,seconds<4_102_444_800 else{return nil}
        return(rt,Double(seconds))
    }
    struct Trace {var rt:UInt32;var groups:[[Float]]}
    /// Preserve original sequential window construction, one dummy on a gap,
    /// and the adjacent-record merge. RT is used for gaps, never as download UTC.
    static func windows(_ traces:[Trace],start:UInt32,period:Int,end:Double)->[Float] {
        var out:[Float]=[],i=0,t0=2.0,t1=Double(period),length=0.0,previous=start
        func rows(_ groups:[[Float]],_ a:Double,_ b:Double)->[Float] {
            var v:[Float]=groups.flatMap{[Float(a),Float(b),$0[0],$0[1],$0[2],Float(0)]}
            for _ in groups.count..<5{v += [Float(a),Float(b),.infinity,.infinity,.infinity,.infinity]}
            return v
        }
        for _ in 0..<3600 {
            guard i<traces.count else{break}
            let trace=traces[i]
            let missing=Int((Double(Int64(trace.rt)-Int64(previous))*0.1/Double(period))-1)
            if missing>0 {
                out += rows([],t0,t1);t0+=Double(period);t1+=Double(period);length+=Double(period)
            }
            var groups=trace.groups;i+=1
            if i<traces.count,Int64(traces[i].rt)-Int64(trace.rt)<=10,
               groups.count+traces[i].groups.count<=5 {groups += traces[i].groups;i+=1}
            out += rows(groups,t0,t1);t0+=Double(period);t1+=Double(period)
            length+=Double(period);previous=trace.rt
        }
        // Original end padding. Never replace these with observed candidates.
        while length+Double(period)<end,out.count/30<3600 {
            length+=Double(period);out += rows([],t0,t1);t0+=Double(period);t1+=Double(period)
        }
        return out
    }
    static func build(context:RecordedContext,records:[RecordedRaw],previousAnchor:Data?)throws->RecordedTensors? {
        guard let age=context.profile.age,let weight=context.profile.weight_kg,
              let height=context.profile.height_cm,let resting=context.profile.resting_hr else {
            throw AppError.message("Completa età, peso, altezza e FC a riposo per il cardio registrato")
        }
        var clock=previousAnchor.flatMap(anchor),start:UInt32?,finish:UInt32?,last:UInt32?
        var ppg:[Trace]=[],accel:[Trace]=[],intensity:[Float]=[],seen=Set<Data>(),digest=Data()
        for record in records {
            let b=[UInt8](record.data)
            guard let rt=tick(b),seen.insert(record.data).inserted else{continue}
            if b[0]==0x41 {
                if start != nil{throw AppError.message("Orologio anello riavviato: tracce salvate, avvia una nuova sessione")}
                clock=nil;start=nil;ppg=[];accel=[];intensity=[];continue
            }
            // Pin the first valid anchor for this context. Later time syncs
            // must not shift already published acquisition instants mid-session.
            if b[0]==0x42 {if clock==nil{clock=anchor(record.data)};continue}
            if b[0]==0x6c,b.count>=8,b[6]==3 {
                if b[7]<=1,start==nil {start=rt}
                else if (2...11).contains(b[7]),start != nil {finish=rt;last=rt;digest.append(record.data);break}
            }
            guard let start,rt>=start else{continue}
            last=rt;digest.append(record.data)
            if b[0]==0x73,(11...20).contains(b.count) {
                var groups:[[Float]]=[]
                for i in stride(from:8,through:b.count-3,by:3){groups.append([Float(b[i]),Float(b[i+1]),Float(b[i+2])])}
                if !groups.isEmpty {let value=Trace(rt:rt,groups:groups);if b[7]==1{ppg.append(value)}else{accel.append(value)}}
            }else if b[0]==0x74,(8...20).contains(b.count) {
                for i in stride(from:6,through:b.count-2,by:2){intensity.append(Float(UInt16(b[i])|UInt16(b[i+1])<<8))}
            }
        }
        guard let start,let last,!ppg.isEmpty,!accel.isEmpty,intensity.count>=2 else{return nil}
        guard let clock,abs(Int64(start)-Int64(clock.0))<Int64(Int32.max) else{
            throw AppError.message("Tracce salvate · attendo un’ancora oraria dell’anello")
        }
        let begin=clock.1+Double(Int64(start)-Int64(clock.0))*0.1
        let end=Double(last-start)*0.1
        guard begin>0,end>0,end<=21_600,ppg.count<=1800,accel.count<=3600,intensity.count<=10800 else {
            throw AppError.message("Sessione cardio oltre i limiti di elaborazione locale; tracce conservate")
        }
        var fixed=context;fixed.revision=0
        let encoder=JSONEncoder();encoder.outputFormatting = .sortedKeys
        let profile=(try? encoder.encode(fixed)) ?? Data()
        digest.append(profile)
        digest.append((try? encoder.encode([Double(clock.0),clock.1])) ?? Data())
        let p=windows(ppg,start:start,period:context.version>=3 ? 12:18,end:end)
        let a=windows(accel,start:start,period:context.version>=3 ? 12:6,end:end)
        return RecordedTensors(ppg:p,accel:a,intensity:intensity,
            timeline:intensity.indices.map{Float(($0+1)*2)},activity:context.activity,
            demographics:[0,Float(age),Float(weight),Float(height),Float(weight/pow(height/100,2)),Float(resting)],
            startedAt:begin,availableUntil:begin+end,endedAt:finish.map{begin+Double($0-start)*0.1},
            digest:Data(SHA256.hash(data:digest)).hex)
    }
}

actor RecordedModelRunner {
    private var model:HPRecordedModel?
    private func bytes(_ values:[Float])->Data{values.withUnsafeBytes{Data($0)}}
    private func floats(_ value:Any?)->[Float] {
        guard let data=value as? Data,data.count%4==0 else{return []}
        return data.withUnsafeBytes{raw in (0..<data.count/4).map{raw.loadUnaligned(fromByteOffset:$0*4,as:Float.self)}}
    }
    func run(_ input:RecordedTensors,context:RecordedContext,revision:Int)throws->RecordedResult {
        if model==nil {
            guard let path=Bundle.main.path(forResource:"whr_2_5_1",ofType:"pt") else{throw AppError.message("Modello cardio privato non installato")}
            model=try HPRecordedModel(path:path)
        }
        guard let model else{throw AppError.message("Modello cardio locale non disponibile")}
        let value=try model.inferPPG(bytes(input.ppg),accel:bytes(input.accel),intensity:bytes(input.intensity),timeline:bytes(input.timeline),activity:input.activity,demographics:bytes(input.demographics))
        let time=floats(value["time"]),hr=floats(value["hr"]),quality=floats(value["quality"])
        guard let check=value["signal_check"] as? NSNumber,[-2.0,-1.0,0.0].contains(check.doubleValue),
              time.count==hr.count,time.count==quality.count,time.count<=21601 else {
            throw AppError.message("Risultato modello cardio incoerente; tracce conservate")
        }
        var points:[RecordedPoint]=[];var previous=Double(-1)
        for i in time.indices {
            let t=Double(time[i]);guard t.isFinite,t>=previous,t>=0 else{throw AppError.message("Tempi del modello cardio incoerenti")};previous=t
            // Keep all raw model outputs locally. Publication clips to ring time.
            points.append(RecordedPoint(offset:t,bpm:hr[i].isFinite ? Double(hr[i]):nil,quality:quality[i].isFinite ? Double(quality[i]):nil))
        }
        return RecordedResult(token:context.token,revision:revision,inputDigest:input.digest,workoutID:context.workoutID,
            startedAt:input.startedAt,availableUntil:input.availableUntil,endedAt:input.endedAt,
            signalCheck:check.intValue,points:points,ppgWindows:input.ppg.count/30,accelWindows:input.accel.count/30,final:input.endedAt != nil)
    }
    func syntheticSelfTest()throws->Int {
        var profile=BodyProfile();profile.age=40;profile.weight_kg=80;profile.height_cm=180;profile.resting_hr=60
        let context=RecordedContext(ring:"synthetic",afterRow:0,version:3,activity:14,profile:profile)
        var ppg:[Float]=[],accel:[Float]=[]
        for i in 0..<15 {
            let start=Float(i*12+2),end=Float((i+1)*12)
            for j in 0..<5 {
                ppg += j==0 ? [start,end,110,110,200,0]:[start,end,.infinity,.infinity,.infinity,.infinity]
                accel += j==0 ? [start,end,70,70,10,0]:[start,end,.infinity,.infinity,.infinity,.infinity]
            }
        }
        let input=RecordedTensors(ppg:ppg,accel:accel,intensity:[Float](repeating:100,count:90),timeline:(1...90).map{Float($0*2)},activity:14,demographics:[0,40,80,180,Float(80/1.8/1.8),60],startedAt:1_600_000_000,availableUntil:1_600_000_180,endedAt:nil,digest:String(repeating:"0",count:64))
        let output=try run(input,context:context,revision:1)
        guard output.points.count==177,output.points.allSatisfy({$0.bpm != nil && $0.quality != nil}) else{throw AppError.message("Verifica sintetica modello non riuscita")}
        return output.points.count
    }
}

@MainActor final class RecordedCardioController:ObservableObject {
    @Published private(set) var result:RecordedResult?
    @Published private(set) var status="Cardio registrato locale pronto"
    @Published private(set) var running=false
    var continuousMode=false{didSet{monitorDiagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"continuous_cardio_enabled":.bool(continuousMode)]))}}
    private var context:RecordedContext?;private var prepared:RecordedContext?;private var working=false;private var dirty=false
    private var next=Date.distantPast;private let store:EventStore;private let runner=RecordedModelRunner()
    private let diagnostic:DiagnosticWriter
    private let serverDiagnostic:DiagnosticWriter
    private let monitorDiagnostic:DiagnosticWriter
    var onResult:((RecordedResult)->Void)?
    init(store:EventStore,diagnosticsURL:URL){
        self.store=store;diagnostic=DiagnosticWriter(url:diagnosticsURL)
        serverDiagnostic=DiagnosticWriter(url:diagnosticsURL.deletingLastPathComponent().appendingPathComponent("recorded-server-status.json"))
        monitorDiagnostic=DiagnosticWriter(url:diagnosticsURL.deletingLastPathComponent().appendingPathComponent("cardio-monitor-status.json"))
        Task{let saved=try? await store.perform{($0.load(RecordedContext.self,key:"recorded.context"),$0.currentRecordedResult(),$0.load(RecordedContext.self,key:"recorded.prepared"))}
            if let saved,context==nil{context=saved.0;result=saved.1;prepared=saved.2;dirty=context != nil;running=saved.0 != nil && saved.1?.final != true;if let result{onResult?(result)};tick()}}
        if ProcessInfo.processInfo.arguments.contains("--recorded-model-self-test") {
            Task{do{let count=try await runner.syntheticSelfTest()
                diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"model_loaded":.bool(true),"synthetic_self_test":.bool(true),"output_count":.number(Double(count)),"personal_data_used":.bool(false)]))
            }catch{diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"synthetic_self_test":.bool(false),"error":.string(error.localizedDescription),"personal_data_used":.bool(false)]))}}
        }
    }
    func prepare(ring:String,version:Int,activity:Int,profile:BodyProfile,workoutID:String?)async throws{
        try profile.validate()
        guard !working,prepared==nil else{throw AppError.message("Elaborazione o avvio cardio in corso")}
        guard profile.age != nil,profile.weight_kg != nil,profile.height_cm != nil,profile.resting_hr != nil else {
            throw AppError.message("Completa età, peso, altezza e FC a riposo nel profilo")
        }
        let row=try await store.perform{try $0.recordedLastRow(ring:ring)}
        let value=RecordedContext(ring:ring,afterRow:row,version:version,activity:activity,profile:profile,workoutID:workoutID)
        try await store.perform{try $0.save(value,key:"recorded.prepared")}
        prepared=value;status="Avvio registrazione cardio in corso"
    }
    func resolvePreparedStart(_ accepted:Bool)async throws{
        guard let value=prepared else{return}
        try await store.perform{try $0.resolvePreparedRecorded(value,accepted:accepted)}
        prepared=nil
        if accepted{context=value;result=nil;running=true;dirty=true;next = .distantPast;status="Attendo le prime tracce cardio dell’anello";tick()}
        else{status="Avvio non completato · pronto per riprovare"}
    }
    func releaseInactiveCapture()async->Bool{
        guard !working,prepared==nil,let context else{return self.context==nil}
        do{
            let saved=try await store.perform{try $0.recordedInput(context:context)}
            guard RecordedBuilder.canReleaseInactive(saved.0,hasResult:!(result?.points.isEmpty ?? true)) else{return false}
            try await store.perform{try $0.releaseRecordedContext(context)}
            self.context=nil;result=nil;running=false;dirty=false;status="Ripresa automatica del cardio";return true
        }catch{return false}
    }
    var canPrepare:Bool{!working && prepared==nil}
    var hasContext:Bool{context != nil}
    func historyChanged(){dirty=true;tick()}
    func observeServer(_ value:JSONValue){
        guard let count=value["point_count"].number,let revision=value["revision"].number else{return}
        serverDiagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"recorded_projection_received":.bool(true),"point_count":.number(count),"revision":.number(revision),"final":value["final"],"model":value["model"],"health_values_exported":.bool(false)]))
    }
    func tick(){
        guard !working,prepared==nil,dirty,Date()>=next,let context else{return}
        working=true;dirty=false;next=Date().addingTimeInterval(15)
        let taskID=UIApplication.shared.beginBackgroundTask(withName:"Cardio registrato locale",expirationHandler:nil)
        Task{defer{working=false;if taskID != .invalid{UIApplication.shared.endBackgroundTask(taskID)}}
            do{
                var context=context
                if continuousMode,result?.final==true {
                    let previousContext=context
                    guard let row=try await store.perform({try $0.recordedNextSessionRow(context:previousContext)}) else{return}
                    context=RecordedContext(ring:context.ring,afterRow:row-1,version:context.version,activity:context.activity,profile:context.profile,workoutID:nil)
                    let resumed=context;try await store.perform{try $0.save(resumed,key:"recorded.context")}
                    self.context=context;result=nil;running=true
                }
                let activeContext=context
                let saved=try await store.perform{try $0.recordedInput(context:activeContext)}
                guard saved.0.count<65_536 else{throw AppError.message("Sessione troppo estesa; tracce salvate")}
                guard let input=try await Task.detached(priority:.utility,operation:{try RecordedBuilder.build(context:activeContext,records:saved.0,previousAnchor:saved.1)}).value else {
                    status="Attendo tracce cardio e movimento"
                    diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"status":.string("awaiting_traces"),"input_record_count":.number(Double(saved.0.count)),"time_anchor_available":.bool(saved.1.flatMap{RecordedBuilder.anchor($0)} != nil),"health_values_exported":.bool(false)]));return
                }
                if result?.inputDigest==input.digest{return}
                let start=Date();let output=try await runner.run(input,context:activeContext,revision:activeContext.revision+1)
                var updated=activeContext;updated.revision=output.revision
                try await store.perform{try $0.commitRecorded(context:updated,result:output)}
                self.context=updated;result=output;running = !output.final
                status=output.signalCheck == -2 ? "Segnale cardio insufficiente · tracce conservate":output.final ? "Cardio registrato · elaborazione finale locale":"Cardio registrato · elaborazione locale"
                diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"model_loaded":.bool(true),"model":.string(output.model),"revision":.number(Double(output.revision)),"output_count":.number(Double(output.points.count)),"displayable_point_count":.number(Double(output.acceptedReadings.count)),"final":.bool(output.final),"elapsed_ms":.number(Date().timeIntervalSince(start)*1000),"health_values_exported":.bool(false)]))
                onResult?(output)
            }catch{status=error.localizedDescription;dirty=true
                diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"status":.string("deferred"),"error":.string(error.localizedDescription),"health_values_exported":.bool(false)]))}
        }
    }
}
