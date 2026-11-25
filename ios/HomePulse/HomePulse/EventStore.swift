import Foundation
import SQLite3
import CryptoKit

final class EventStore: @unchecked Sendable {
    private var db:OpaquePointer?
    private let lock=NSRecursiveLock()
    private var retentionRunning=false;private var lastRetentionReport=0.0
    private let ioQueue=DispatchQueue(label:"HomePulse.archive",qos:.userInitiated)
    private let transient=unsafeBitCast(-1,to:sqlite3_destructor_type.self)
    init(url:URL) throws {
        try FileManager.default.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true,attributes:[.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication])
        guard sqlite3_open_v2(url.path,&db,SQLITE_OPEN_CREATE|SQLITE_OPEN_READWRITE|SQLITE_OPEN_FULLMUTEX,nil)==SQLITE_OK else {throw AppError.message("Archivio locale non disponibile")}
        try run("PRAGMA journal_mode=WAL");try run("PRAGMA synchronous=FULL");try run("PRAGMA foreign_keys=ON")
        try run("CREATE TABLE IF NOT EXISTS journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,captured_at REAL NOT NULL,kind TEXT NOT NULL,payload BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY REFERENCES journal(event_id),batch_id TEXT)")
        let columns=try statement("PRAGMA table_info(outbox)")
        var hasSequence=false
        while sqlite3_step(columns)==SQLITE_ROW{if text(columns,1)=="journal_sequence"{hasSequence=true}}
        sqlite3_finalize(columns)
        if !hasSequence{try run("ALTER TABLE outbox ADD COLUMN journal_sequence INTEGER")}
        try run("UPDATE outbox SET journal_sequence=(SELECT sequence FROM journal WHERE journal.event_id=outbox.event_id) WHERE journal_sequence IS NULL")
        try run("CREATE INDEX IF NOT EXISTS outbox_pending_order ON outbox(batch_id,journal_sequence,event_id)")
        try run("CREATE INDEX IF NOT EXISTS journal_recent_upload ON journal(kind,captured_at,event_id)")
        try run("DROP INDEX IF EXISTS outbox_pending")
        try run("DROP INDEX IF EXISTS journal_upload_metadata")
        try run("CREATE TRIGGER IF NOT EXISTS outbox_legacy_sequence AFTER INSERT ON outbox WHEN NEW.journal_sequence IS NULL BEGIN UPDATE outbox SET journal_sequence=(SELECT sequence FROM journal WHERE event_id=NEW.event_id) WHERE event_id=NEW.event_id; END")
        try run("CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY,body BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,body BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS history_records(id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,ring TEXT,raw BLOB,received REAL,steps INTEGER,temp REAL,motion INTEGER)")
        try run("CREATE INDEX IF NOT EXISTS history_recorded_lookup ON history_records(ring,substr(raw,1,1))")
        try run("CREATE TABLE IF NOT EXISTS recorded_segments(token TEXT PRIMARY KEY,started REAL NOT NULL,through REAL NOT NULL,body BLOB NOT NULL)")
        try run("CREATE INDEX IF NOT EXISTS recorded_segments_time ON recorded_segments(started,through)")
        try run("INSERT OR IGNORE INTO recorded_segments(token,started,through,body) SELECT substr(key,17),json_extract(CAST(value AS TEXT),'$.startedAt'),json_extract(CAST(value AS TEXT),'$.availableUntil'),value FROM kv WHERE key LIKE 'recorded.result.%' AND json_valid(CAST(value AS TEXT))")
        try run("CREATE TABLE IF NOT EXISTS history_receipts(digest BLOB PRIMARY KEY) WITHOUT ROWID")
        try run("CREATE TABLE IF NOT EXISTS retention_state(name TEXT PRIMARY KEY,value INTEGER NOT NULL)")
        try run("INSERT OR IGNORE INTO retention_state VALUES('history.highwater',(SELECT coalesce(max(rowid),0) FROM history_records))")
        try run("CREATE TABLE IF NOT EXISTS recorded_receipts(token TEXT PRIMARY KEY,revision INTEGER NOT NULL,final INTEGER NOT NULL)")
        let historyColumns=try statement("PRAGMA table_info(history_records)");var hasSynced=false;var stableHistory=false
        while sqlite3_step(historyColumns)==SQLITE_ROW{if text(historyColumns,1)=="synced"{hasSynced=true};if text(historyColumns,1)=="id"{stableHistory=true}};sqlite3_finalize(historyColumns)
        if !hasSynced{try run("ALTER TABLE history_records ADD COLUMN synced INTEGER NOT NULL DEFAULT 0")}
        if !stableHistory{try transaction{
            try run("CREATE TABLE history_records_stable(id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,ring TEXT,raw BLOB,received REAL,steps INTEGER,temp REAL,motion INTEGER,synced INTEGER NOT NULL DEFAULT 0)")
            try run("INSERT INTO history_records_stable SELECT rowid,digest,ring,raw,received,steps,temp,motion,synced FROM history_records")
            try run("DROP TABLE history_records");try run("ALTER TABLE history_records_stable RENAME TO history_records")
            try run("CREATE INDEX history_recorded_lookup ON history_records(ring,substr(raw,1,1))")
        }}
        try run("CREATE INDEX IF NOT EXISTS history_sync_state ON history_records(synced,ring)")
        try run("CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,bpm INTEGER NOT NULL)")
        try run("CREATE INDEX IF NOT EXISTS readings_time ON readings(at,id)")
        try? FileManager.default.setAttributes([.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication],ofItemAtPath:url.path)
    }
    deinit {sqlite3_close(db)}
    func perform<T:Sendable>(_ operation:@escaping @Sendable(EventStore)throws->T)async throws->T{
        try await withCheckedThrowingContinuation{continuation in
            ioQueue.async{do{continuation.resume(returning:try operation(self))}catch{continuation.resume(throwing:error)}}
        }
    }
    func enqueue(_ operation:@escaping @Sendable(EventStore)throws->Void,onError:(@Sendable(Error)->Void)?=nil){
        ioQueue.async{do{try operation(self)}catch{onError?(error)}}
    }
    #if DEBUG
    func holdArchiveForUITest(seconds:TimeInterval,onStart:()->Void={}){guarded{onStart();Thread.sleep(forTimeInterval:seconds)}}
    #endif
    private func guarded<T>(_ body:() throws->T) rethrows->T {lock.lock();defer{lock.unlock()};return try body()}
    private func statement(_ sql:String) throws->OpaquePointer {
        var s:OpaquePointer?;guard sqlite3_prepare_v2(db,sql,-1,&s,nil)==SQLITE_OK,let s else {throw AppError.message("Operazione archivio non riuscita")};return s
    }
    private func bind(_ text:String,_ s:OpaquePointer,_ index:Int32){sqlite3_bind_text(s,index,text,-1,transient)}
    private func bind(_ data:Data,_ s:OpaquePointer,_ index:Int32){_ = data.withUnsafeBytes{sqlite3_bind_blob(s,index,$0.baseAddress,Int32(data.count),transient)}}
    private func data(_ s:OpaquePointer,_ index:Int32)->Data {let n=Int(sqlite3_column_bytes(s,index));return n>0 ? Data(bytes:sqlite3_column_blob(s,index)!,count:n):Data()}
    private func text(_ s:OpaquePointer,_ index:Int32)->String {String(cString:sqlite3_column_text(s,index))}
    private func done(_ s:OpaquePointer) throws {guard sqlite3_step(s)==SQLITE_DONE else {throw AppError.message("Scrittura locale non riuscita")}}
    private func run(_ sql:String) throws {guard sqlite3_exec(db,sql,nil,nil,nil)==SQLITE_OK else {throw AppError.message("Archivio locale non scrivibile")}}
    private func transaction<T>(_ body:()throws->T) throws->T {
        try run("BEGIN IMMEDIATE")
        do {let v=try body();try run("COMMIT");return v} catch {try? run("ROLLBACK");throw error}
    }
    @discardableResult func append(kind:String,payload:JSONValue,at:Date=Date(),eventID:UUID=UUID()) throws->StoredEvent {
        try guarded {try transaction {try insert(kind:kind,payload:payload,at:at,eventID:eventID)}}
    }
    private func insert(kind:String,payload:JSONValue,at:Date,eventID:UUID) throws->StoredEvent {
        let id=eventID.uuidString,s=try statement("INSERT INTO journal(event_id,captured_at,kind,payload) VALUES(?,?,?,?)");defer{sqlite3_finalize(s)}
        bind(id,s,1);sqlite3_bind_double(s,2,at.timeIntervalSince1970);bind(kind,s,3);bind(try JSONEncoder().encode(payload),s,4);try done(s)
        let seq=sqlite3_last_insert_rowid(db)
        let q=try statement("INSERT INTO outbox(event_id,journal_sequence) VALUES(?,?)");defer{sqlite3_finalize(q)};bind(id,q,1);sqlite3_bind_int64(q,2,seq);try done(q)
        return StoredEvent(event_id:id,sequence:seq,captured_at:at.timeIntervalSince1970,kind:kind,payload:payload)
    }
    func save<T:Encodable>(_ value:T,key:String) throws {let encoder=JSONEncoder();encoder.outputFormatting = .sortedKeys;let body=try encoder.encode(value);try guarded{try put(body,key:key)}}
    private func put(_ data:Data,key:String) throws {let s=try statement("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE kv.value != excluded.value");defer{sqlite3_finalize(s)};bind(key,s,1);bind(data,s,2);try done(s)}
    func load<T:Decodable>(_ type:T.Type,key:String)->T? {guarded{do{let s=try statement("SELECT value FROM kv WHERE key=?");defer{sqlite3_finalize(s)};bind(key,s,1);guard sqlite3_step(s)==SQLITE_ROW else{return nil};return try JSONDecoder().decode(type,from:data(s,0))}catch{return nil}}}
    func workout(_ session:WorkoutSession,payload:JSONValue,active:Bool=true) throws {
        try guarded {try transaction {
            let body=try JSONEncoder().encode(session)
            if active {try put(body,key:"activeWorkout")}
            let s=try statement("INSERT OR REPLACE INTO sessions(id,body) VALUES(?,?)");defer{sqlite3_finalize(s)};bind(session.id.uuidString,s,1);bind(body,s,2);try done(s)
            _=try insert(kind:"workout",payload:payload,at:Date(),eventID:UUID())
        }}
    }
    var pendingWorkoutCount:Int{guarded{(try? count("SELECT count(*) FROM outbox o JOIN journal j USING(event_id) WHERE j.kind='workout'")) ?? Int.max}}
    @discardableResult func reconcileSessions(_ sessions:[WorkoutSession],active:WorkoutSession?)throws->Bool{
        try guarded{try transaction{
            guard try count("SELECT count(*) FROM outbox o JOIN journal j USING(event_id) WHERE j.kind='workout'")==0 else{return false}
            try run("DELETE FROM sessions")
            for session in sessions where diaryAllows(session) && session.id==active?.id{let s=try statement("INSERT OR REPLACE INTO sessions(id,body) VALUES(?,?)");defer{sqlite3_finalize(s)};bind(session.id.uuidString,s,1);bind(try JSONEncoder().encode(session),s,2);try done(s)}
            try put(try JSONEncoder().encode(active),key:"activeWorkout");return true
        }}
    }
    func sessions()->[WorkoutSession] {guarded{do{let s=try statement("SELECT body FROM sessions ORDER BY rowid DESC LIMIT 300");defer{sqlite3_finalize(s)};var a:[WorkoutSession]=[];while sqlite3_step(s)==SQLITE_ROW{if let v=try? JSONDecoder().decode(WorkoutSession.self,from:data(s,0)),diaryAllows(v){a.append(v)}};return a.sorted{$0.startedAt>$1.startedAt}}catch{return []}}}
    func diaryAllows(_ session:WorkoutSession)->Bool{(load([DiaryCleanup].self,key:"diary.cleanups") ?? []).allSatisfy{$0.allows(session)}}
    func applyDiaryCleanup(_ rule:DiaryCleanup,pending:Bool=false)throws{try guarded{try transaction{
        guard rule.valid else{throw AppError.message("Intervallo diario non valido")}
        var rules=load([DiaryCleanup].self,key:"diary.cleanups") ?? []
        if !rules.contains(rule){rules.append(rule);try save(rules,key:"diary.cleanups")}
        if pending{try save(rule,key:"diary.cleanup.pending")}
        let rows=try statement("SELECT id,body FROM sessions");var remove:[String]=[]
        while sqlite3_step(rows)==SQLITE_ROW{if let value=try? JSONDecoder().decode(WorkoutSession.self,from:data(rows,1)),!diaryAllows(value){remove.append(text(rows,0))}}
        sqlite3_finalize(rows)
        for id in remove{let q=try statement("DELETE FROM sessions WHERE id=?");bind(id,q,1);try done(q);sqlite3_finalize(q)}
        if let active=load(WorkoutSession.self,key:"activeWorkout"),!diaryAllows(active){try save(Optional<WorkoutSession>.none,key:"activeWorkout")}
    }}}
    func diaryLocal(start:Date,end:Date)->JSONValue{guarded{
        var rows=recordedResults(start:start,end:end).flatMap{$0.acceptedReadings}.filter{$0.date>=start && $0.date<=end}
        if let q=try? statement("SELECT captured_at,payload FROM journal j JOIN outbox o USING(event_id) WHERE j.kind='sensor_packet' AND captured_at>=? AND captured_at<=? ORDER BY captured_at,sequence"){
            sqlite3_bind_double(q,1,start.timeIntervalSince1970);sqlite3_bind_double(q,2,end.timeIntervalSince1970)
            while sqlite3_step(q)==SQLITE_ROW{
                if let payload=try? JSONDecoder().decode(JSONValue.self,from:data(q,1)),let hex=payload["raw_hex"].string,let raw=Data(hex:hex),let reading=try? OuraWire.live(raw,api:payload["api_version"].array.compactMap{$0.number.map{Int($0)}}),let bpm=reading.bpm,(20...250).contains(bpm){rows.append(Reading(date:Date(timeIntervalSince1970:sqlite3_column_double(q,0)),bpm:bpm))}
            };sqlite3_finalize(q)
        }
        let sorted=rows.sorted{$0.date<$1.date};var segment=0;var previous:Date?;var points:[ChartPoint]=[]
        for row in sorted{if let previous,row.date.timeIntervalSince(previous)>8{segment+=1};previous=row.date;points.append(ChartPoint(date:row.date,value:Double(row.bpm),segment:segment))}
        let bounded=ChartSeries.bound(points)
        let average=sorted.isEmpty ? nil:Double(sorted.reduce(0){$0+$1.bpm})/Double(sorted.count)
        let stats:JSONValue = .object(["average_bpm":average.map{.number($0)} ?? .null,"max_bpm":sorted.map(\.bpm).max().map{.number(Double($0))} ?? .null])
        return .object(["stats":stats,"heart_rate":.array(bounded.map{.object(["t":.number($0.date.timeIntervalSince1970),"v":.number($0.value),"segment":.number(Double($0.segment ?? 0))])}),"window_start":.number(start.timeIntervalSince1970),"window_end":.number(end.timeIntervalSince1970),"offline":.bool(true)])
    }}
    func addReading(_ reading:Reading) throws {try guarded{let s=try statement("INSERT INTO readings(at,bpm) VALUES(?,?)");defer{sqlite3_finalize(s)};sqlite3_bind_double(s,1,reading.date.timeIntervalSince1970);sqlite3_bind_int(s,2,Int32(reading.bpm));try done(s)}}
    func readings(limit:Int=500)->[Reading] {guarded{do{let s=try statement("SELECT at,bpm FROM readings ORDER BY id DESC LIMIT ?");defer{sqlite3_finalize(s)};sqlite3_bind_int(s,1,Int32(limit));var a:[Reading]=[];while sqlite3_step(s)==SQLITE_ROW{a.append(Reading(date:Date(timeIntervalSince1970:sqlite3_column_double(s,0)),bpm:Int(sqlite3_column_int(s,1))))};return a.reversed()}catch{return []}}}
    var pendingCount:Int {guarded{(try? count("SELECT count(*) FROM outbox")) ?? 0}}
    var journalCount:Int {guarded{(try? count("SELECT count(*) FROM journal")) ?? 0}}
    private func count(_ sql:String) throws->Int {let s=try statement(sql);defer{sqlite3_finalize(s)};return sqlite3_step(s)==SQLITE_ROW ? Int(sqlite3_column_int(s,0)):0}
    func prepareBatch(limit:Int=100,prioritizeLive:Bool=false) throws->EventBatch? {try guarded{try transaction{
        let old=try statement("SELECT body FROM batches ORDER BY rowid LIMIT 1");defer{sqlite3_finalize(old)}
        if sqlite3_step(old)==SQLITE_ROW{return try JSONDecoder().decode(EventBatch.self,from:data(old,0))}
        let bound=max(1,min(1000,limit));var ids:[String]=[];var seen=Set<String>()
        func selectIDs(_ sql:String,after:Double?=nil)throws{
            guard ids.count<bound else{return}
            let s=try statement(sql);defer{sqlite3_finalize(s)}
            if let after{sqlite3_bind_double(s,1,after)}
            sqlite3_bind_int(s,after==nil ? 1:2,Int32(bound))
            var status=sqlite3_step(s)
            while status==SQLITE_ROW{let id=text(s,0);if ids.count<bound,seen.insert(id).inserted{ids.append(id)};status=sqlite3_step(s)}
            guard status==SQLITE_DONE else{throw AppError.message("Lettura della coda non riuscita: dati conservati")}
        }
        if prioritizeLive{
            try selectIDs("SELECT j.event_id FROM journal j INDEXED BY journal_recent_upload JOIN outbox o ON o.event_id=j.event_id WHERE j.kind='sensor_packet' AND j.captured_at>=? AND o.batch_id IS NULL ORDER BY j.sequence LIMIT ?",after:Date().timeIntervalSince1970-8)
            try selectIDs("SELECT j.event_id FROM journal j INDEXED BY journal_recent_upload JOIN outbox o ON o.event_id=j.event_id WHERE j.kind='workout' AND o.batch_id IS NULL ORDER BY j.sequence LIMIT ?")
            try selectIDs("SELECT j.event_id FROM journal j INDEXED BY journal_recent_upload JOIN outbox o ON o.event_id=j.event_id WHERE j.kind='recorded_cardio' AND o.batch_id IS NULL ORDER BY j.sequence LIMIT ?")
        }
        // FIFO comes straight from the pending index: no repeated sorting or
        // scanning of the backlog, and payloads are read only for selected IDs.
        try selectIDs("SELECT event_id FROM outbox INDEXED BY outbox_pending_order WHERE batch_id IS NULL ORDER BY journal_sequence LIMIT ?")
        let s=try statement("SELECT event_id,sequence,captured_at,kind,payload FROM journal WHERE event_id=?");defer{sqlite3_finalize(s)}
        var events:[StoredEvent]=[]
        for id in ids{sqlite3_reset(s);sqlite3_clear_bindings(s);bind(id,s,1)
            guard sqlite3_step(s)==SQLITE_ROW else{throw AppError.message("Evento in coda non leggibile: archivio conservato")}
            events.append(StoredEvent(event_id:text(s,0),sequence:sqlite3_column_int64(s,1),captured_at:sqlite3_column_double(s,2),kind:text(s,3),payload:try JSONDecoder().decode(JSONValue.self,from:data(s,4))))
        }
        guard !events.isEmpty else{return nil};let batch=EventBatch(batch_id:UUID().uuidString,events:events)
        let b=try statement("INSERT INTO batches(batch_id,body) VALUES(?,?)");defer{sqlite3_finalize(b)};bind(batch.batch_id,b,1);bind(try JSONEncoder().encode(batch),b,2);try done(b)
        let claim=try statement("UPDATE outbox SET batch_id=? WHERE event_id=?");defer{sqlite3_finalize(claim)}
        for event in events{sqlite3_reset(claim);sqlite3_clear_bindings(claim);bind(batch.batch_id,claim,1);bind(event.event_id,claim,2);try done(claim)}
        return batch
    }}}
    @discardableResult func acknowledge(batchID:String,accepted:[String]) throws->Bool {try guarded{try transaction{
        let s=try statement("SELECT body FROM batches WHERE batch_id=?");defer{sqlite3_finalize(s)};bind(batchID,s,1)
        guard sqlite3_step(s)==SQLITE_ROW else{return true}
        let batch=try JSONDecoder().decode(EventBatch.self,from:data(s,0));let expected=Set(batch.events.map{$0.event_id})
        guard Set(accepted).isSubset(of:expected) else {throw AppError.message("Conferma server incoerente: dati locali conservati")}
        let acknowledge=try statement("DELETE FROM outbox WHERE event_id=? AND batch_id=?");defer{sqlite3_finalize(acknowledge)}
        for id in Set(accepted){sqlite3_reset(acknowledge);sqlite3_clear_bindings(acknowledge);bind(id,acknowledge,1);bind(batchID,acknowledge,2);try done(acknowledge)}
        for event in batch.events where Set(accepted).contains(event.event_id){try markServerSaved(event)}
        let remove=try statement("DELETE FROM journal WHERE event_id=? AND NOT EXISTS(SELECT 1 FROM outbox WHERE outbox.event_id=journal.event_id)");defer{sqlite3_finalize(remove)}
        for id in Set(accepted){sqlite3_reset(remove);sqlite3_clear_bindings(remove);bind(id,remove,1);try done(remove)}
        try pruneServerCopies()
        let q=try statement("UPDATE outbox SET batch_id=NULL WHERE batch_id=?");defer{sqlite3_finalize(q)};bind(batchID,q,1);try done(q)
        let b=try statement("DELETE FROM batches WHERE batch_id=?");defer{sqlite3_finalize(b)};bind(batchID,b,1);try done(b)
        return expected.isSubset(of:Set(accepted))
    }}}
    func ingestPackets(_ frames:[Data],api:[Int],ring:String?,at:Date,opaque:Data?=nil)throws{
        try guarded{try transaction{
            for frame in frames where !OuraWire.sensitive(frame){
                if (try? OuraWire.cursor(frame)) != nil{
                    try archiveFrames([frame],api:api,ring:ring,at:at)
                }else{
                    var payload:[String:JSONValue]=["raw_hex":.string(frame.hex),"api_version":.array(api.map{.number(Double($0))})]
                    if let ring{payload["logical_ring_id"] = .string(ring)}
                    _=try insert(kind:"sensor_packet",payload:.object(payload),at:at,eventID:UUID())
                }
            }
            if let opaque{
                var payload:[String:JSONValue]=["raw_hex":.string(opaque.hex),"api_version":.array(api.map{.number(Double($0))}),"transport_unparsed":.bool(true)]
                if let ring{payload["logical_ring_id"] = .string(ring)}
                _=try insert(kind:"sensor_packet",payload:.object(payload),at:at,eventID:UUID())
            }
        }}
    }
    func archiveHistory(frames:[Data],nextCursor:UInt32,key:String,api:[Int],ring:String?) throws {try guarded{try transaction{
        try archiveFrames(frames,api:api,ring:ring,at:Date())
        try put(try JSONEncoder().encode(nextCursor),key:key)
    }}}
    private func archiveFrames(_ frames:[Data],api:[Int],ring:String?,at:Date)throws{
        for frame in frames {
            let bytes=[UInt8](frame)
            let digest=Data(SHA256.hash(data:Data((ring ?? "unknown").utf8)+frame)).hex
            if let bytes=Data(hex:digest){let seen=try statement("SELECT 1 FROM history_receipts WHERE digest=?");bind(bytes,seen,1);let known=sqlite3_step(seen)==SQLITE_ROW;sqlite3_finalize(seen);if known{continue}}
            let h=try statement("INSERT OR IGNORE INTO history_records(digest,ring,raw,received,steps,temp,motion,rowid) VALUES(?,?,?,?,?,?,?,(SELECT value+1 FROM retention_state WHERE name='history.highwater'))")
            bind(digest,h,1);bind(ring ?? "unknown",h,2);bind(frame,h,3);sqlite3_bind_double(h,4,at.timeIntervalSince1970)
            if bytes.count>=7,bytes[0]==0x50 {sqlite3_bind_int(h,5,Int32(bytes[6]))}else{sqlite3_bind_null(h,5)}
            if (10...20).contains(bytes.count),bytes[0]==0x46,bytes.count%2==0 {let value=Int16(bitPattern:UInt16(bytes[6])|(UInt16(bytes[7])<<8));if value != Int16.min{sqlite3_bind_double(h,6,Double(value)/100)}else{sqlite3_bind_null(h,6)}}else{sqlite3_bind_null(h,6)}
            if (10...12).contains(bytes.count),bytes[0]==0x47 {sqlite3_bind_int(h,7,Int32(bytes[6]&31))}else{sqlite3_bind_null(h,7)}
            try done(h);let inserted=sqlite3_changes(db)>0;sqlite3_finalize(h)
            if !inserted {continue}
            try run("UPDATE retention_state SET value=last_insert_rowid() WHERE name='history.highwater'")
            var p:[String:JSONValue]=["raw_hex":.string(frame.hex),"api_version":.array(api.map{.number(Double($0))})]
            if let ring {p["logical_ring_id"] = .string(ring)}
            var identity=[UInt8](Data(SHA256.hash(data:Data(digest.utf8))).prefix(16));identity[6]=(identity[6]&15)|0x50;identity[8]=(identity[8]&63)|0x80
            let eventID=UUID(uuidString:NSUUID(uuidBytes:identity).uuidString)!
            _=try insert(kind:"ring_history",payload:.object(p),at:at,eventID:eventID)
        }
    }
    func metrics()->HistoricalMetrics {guarded{do{
        var result=HistoricalMetrics()
        let a=try statement("SELECT temp,received FROM history_records WHERE temp IS NOT NULL ORDER BY rowid DESC LIMIT 1");defer{sqlite3_finalize(a)}
        if sqlite3_step(a)==SQLITE_ROW{result.temperatureC=sqlite3_column_double(a,0);result.receivedAt=Date(timeIntervalSince1970:sqlite3_column_double(a,1))}
        let b=try statement("SELECT sum(steps),count(steps) FROM history_records");defer{sqlite3_finalize(b)}
        if sqlite3_step(b)==SQLITE_ROW,sqlite3_column_int(b,1)>0{result.steps=Int(sqlite3_column_int64(b,0))}
        let c=try statement("SELECT motion FROM history_records WHERE motion IS NOT NULL ORDER BY rowid DESC LIMIT 1");defer{sqlite3_finalize(c)}
        if sqlite3_step(c)==SQLITE_ROW{result.motionSeconds=Int(sqlite3_column_int(c,0))};return result
    }catch{return HistoricalMetrics()}}}

    func recordedLastRow(ring:String)throws->Int64{try guarded{
        let s=try statement("SELECT max(coalesce((SELECT max(rowid) FROM history_records WHERE ring=?),0),(SELECT value FROM retention_state WHERE name='history.highwater'))");defer{sqlite3_finalize(s)}
        bind(ring,s,1);guard sqlite3_step(s)==SQLITE_ROW else{throw AppError.message("Archivio cardio non leggibile")}
        return sqlite3_column_int64(s,0)
    }}
    func recordedNextSessionRow(context:RecordedContext)throws->Int64?{try guarded{
        let q=try statement("SELECT rowid,raw FROM history_records WHERE ring=? AND rowid>? AND substr(raw,1,1)=x'6c' ORDER BY rowid");defer{sqlite3_finalize(q)}
        bind(context.ring,q,1);sqlite3_bind_int64(q,2,context.afterRow)
        var sawStart=false,sawEnd=false
        while sqlite3_step(q)==SQLITE_ROW{
            let b=[UInt8](data(q,1));guard b.count>=8,b[6]==3 else{continue}
            if b[7]<=1{
                if sawEnd{return sqlite3_column_int64(q,0)}
                sawStart=true
            }else if sawStart,(2...11).contains(b[7]){sawEnd=true}
        }
        return nil
    }}
    func recordedInput(context:RecordedContext)throws->([RecordedRaw],Data?){try guarded{
        let a=try statement("SELECT raw FROM history_records WHERE ring=? AND rowid<=? AND substr(raw,1,1) IN (x'41',x'42') ORDER BY rowid DESC LIMIT 1");defer{sqlite3_finalize(a)}
        bind(context.ring,a,1);sqlite3_bind_int64(a,2,context.afterRow)
        let anchor=sqlite3_step(a)==SQLITE_ROW ? data(a,0):nil
        let s=try statement("SELECT rowid,raw FROM history_records WHERE ring=? AND rowid>? AND substr(raw,1,1) IN (x'41',x'42',x'6c',x'73',x'74') ORDER BY rowid LIMIT 65536");defer{sqlite3_finalize(s)}
        bind(context.ring,s,1);sqlite3_bind_int64(s,2,context.afterRow)
        var values:[RecordedRaw]=[];var state=sqlite3_step(s)
        while state==SQLITE_ROW{values.append(RecordedRaw(row:sqlite3_column_int64(s,0),data:data(s,1)));state=sqlite3_step(s)}
        guard state==SQLITE_DONE else{throw AppError.message("Tracce cardio non leggibili")}
        return(values,anchor)
    }}
    func commitRecorded(context:RecordedContext,result:RecordedResult)throws{try guarded{try transaction{
        try put(try JSONEncoder().encode(context),key:"recorded.context")
        try put(try JSONEncoder().encode(result.token),key:"recorded.latest.token")
        let body=try JSONEncoder().encode(result)
        let segment=try statement("INSERT INTO recorded_segments(token,started,through,body) VALUES(?,?,?,?) ON CONFLICT(token) DO UPDATE SET started=excluded.started,through=excluded.through,body=excluded.body");defer{sqlite3_finalize(segment)}
        bind(result.token,segment,1);sqlite3_bind_double(segment,2,result.startedAt);sqlite3_bind_double(segment,3,result.availableUntil);bind(body,segment,4);try done(segment)
        _=try insert(kind:"recorded_cardio",payload:result.payload,at:Date(),eventID:UUID())
    }}}
    func recordedResults(start:Date,end:Date)->[RecordedResult]{guarded{
        do{
            let s=try statement("SELECT body FROM recorded_segments WHERE started<=? AND through>=? ORDER BY started,token");defer{sqlite3_finalize(s)}
            sqlite3_bind_double(s,1,end.timeIntervalSince1970);sqlite3_bind_double(s,2,start.timeIntervalSince1970)
            var values:[RecordedResult]=[]
            while sqlite3_step(s)==SQLITE_ROW{values.append(try JSONDecoder().decode(RecordedResult.self,from:data(s,0)))}
            return values
        }catch{return []}
    }}
    func recordedResults(workoutID:String)->[RecordedResult]{guarded{
        do{let s=try statement("SELECT body FROM recorded_segments WHERE json_extract(CAST(body AS TEXT),'$.workoutID')=? ORDER BY started");defer{sqlite3_finalize(s)};bind(workoutID,s,1);var rows:[RecordedResult]=[];while sqlite3_step(s)==SQLITE_ROW{rows.append(try JSONDecoder().decode(RecordedResult.self,from:data(s,0)))};return rows}catch{return []}
    }}

    func currentRecordedResult()->RecordedResult?{guarded{
        if let token=load(String.self,key:"recorded.latest.token"),let s=try? statement("SELECT body FROM recorded_segments WHERE token=?"){
            defer{sqlite3_finalize(s)};bind(token,s,1)
            if sqlite3_step(s)==SQLITE_ROW{return try? JSONDecoder().decode(RecordedResult.self,from:data(s,0))}
        }
        return load(RecordedResult.self,key:"recorded.latest")
    }}
    var activeBatchIDs:Set<String>{guarded{do{let s=try statement("SELECT batch_id FROM batches");defer{sqlite3_finalize(s)};var ids=Set<String>();while sqlite3_step(s)==SQLITE_ROW{ids.insert(text(s,0))};return ids}catch{return []}}}
    private func markServerSaved(_ event:StoredEvent)throws{
        if event.kind=="ring_history",let hex=event.payload["raw_hex"].string,let raw=Data(hex:hex){
            let ring=event.payload["logical_ring_id"].string ?? "unknown"
            let digest=Data(SHA256.hash(data:Data(ring.utf8)+raw))
            let receipt=try statement("INSERT OR IGNORE INTO history_receipts VALUES(?)");defer{sqlite3_finalize(receipt)};bind(digest,receipt,1);try done(receipt)
            let q=try statement("UPDATE history_records SET synced=1 WHERE digest=?");defer{sqlite3_finalize(q)};bind(digest.hex,q,1);try done(q)
        }else if event.kind=="recorded_cardio",let token=event.payload["token"].string,let revision=event.payload["revision"].number{
            let q=try statement("INSERT INTO recorded_receipts VALUES(?,?,?) ON CONFLICT(token) DO UPDATE SET revision=excluded.revision,final=excluded.final WHERE excluded.revision>=recorded_receipts.revision");defer{sqlite3_finalize(q)}
            bind(token,q,1);sqlite3_bind_int(q,2,Int32(revision));sqlite3_bind_int(q,3,event.payload["final"] == .bool(true) ? 1:0);try done(q)
        }
    }
    private func pruneServerCopies()throws{
        // Keep the in-flight model window and its exact preceding time/reset
        // anchor. Everything else can be recovered from the authoritative server.
        let context=load(RecordedContext.self,key:"recorded.context"),latest=currentRecordedResult()
        let current=context?.token ?? ""
        let receipt=try statement("SELECT revision,final FROM recorded_receipts WHERE token=?");bind(current,receipt,1)
        let terminalSaved=sqlite3_step(receipt)==SQLITE_ROW && sqlite3_column_int(receipt,1)==1 && Int(sqlite3_column_int(receipt,0)) >= (latest?.revision ?? Int.max)
        sqlite3_finalize(receipt)
        var protected:[Int64]=[]
        let anchors=try statement("SELECT max(rowid) FROM history_records WHERE substr(raw,1,1) IN (x'41',x'42') GROUP BY ring")
        while sqlite3_step(anchors)==SQLITE_ROW{protected.append(sqlite3_column_int64(anchors,0))};sqlite3_finalize(anchors)
        if let context{
            let q=try statement("SELECT max(rowid) FROM history_records WHERE ring=? AND rowid<=? AND substr(raw,1,1) IN (x'41',x'42')");bind(context.ring,q,1);sqlite3_bind_int64(q,2,context.afterRow)
            if sqlite3_step(q)==SQLITE_ROW{protected.append(sqlite3_column_int64(q,0))};sqlite3_finalize(q)
        }
        let exclusions=protected.isEmpty ? "0":protected.map(String.init).joined(separator:",")
        var sql="DELETE FROM history_records WHERE synced=1 AND rowid NOT IN ("+exclusions+")"
        if let context{
            sql += terminalSaved ? " AND NOT(ring=? AND rowid>? AND substr(raw,1,1)=x'6c')":" AND NOT(ring=? AND rowid>? AND substr(raw,1,1) IN (x'41',x'42',x'6c',x'73',x'74'))"
            let q=try statement(sql);bind(context.ring,q,1);sqlite3_bind_int64(q,2,context.afterRow);try done(q);sqlite3_finalize(q)
        }else{try run(sql)}
        // An acknowledged final current segment needs only transition metadata.
        if terminalSaved,var result=latest,!result.points.isEmpty{
            result.points=[];let q=try statement("UPDATE recorded_segments SET body=? WHERE token=?");bind(try JSONEncoder().encode(result),q,1);bind(current,q,2);try done(q);sqlite3_finalize(q)
        }
        let q=try statement("DELETE FROM recorded_segments WHERE token!=? AND EXISTS(SELECT 1 FROM recorded_receipts r WHERE r.token=recorded_segments.token AND r.revision>=json_extract(CAST(body AS TEXT),'$.revision'))");bind(current,q,1);try done(q);sqlite3_finalize(q)
        let sessions=try statement("SELECT id,body FROM sessions");var remove:[String]=[]
        while sqlite3_step(sessions)==SQLITE_ROW{
            let value=try JSONDecoder().decode(WorkoutSession.self,from:data(sessions,1))
            if ["running","paused"].contains(value.state){continue}
            let pending=try statement("SELECT 1 FROM journal j JOIN outbox o USING(event_id) WHERE j.kind='workout' AND json_extract(CAST(j.payload AS TEXT),'$.session_id')=? LIMIT 1")
            bind(value.remoteID ?? value.id.uuidString,pending,1);let unsent=sqlite3_step(pending)==SQLITE_ROW;sqlite3_finalize(pending)
            if !unsent{remove.append(text(sessions,0))}
        };sqlite3_finalize(sessions)
        for id in remove{let q=try statement("DELETE FROM sessions WHERE id=?");bind(id,q,1);try done(q);sqlite3_finalize(q)}
        if let active=load(WorkoutSession.self,key:"activeWorkout"),!["running","paused"].contains(active.state){try run("DELETE FROM kv WHERE key='activeWorkout'")}
    }
    func retentionStep(limit:Int=4000)throws->Bool{try guarded{
        try transaction{
            try run("INSERT OR IGNORE INTO retention_state VALUES('legacy.end',(SELECT coalesce(max(sequence),0) FROM journal))")
            try run("INSERT OR IGNORE INTO retention_state VALUES('legacy.cursor',0)")
            if load(String.self,key:"recorded.latest.token")==nil,let latest=load(RecordedResult.self,key:"recorded.latest"){try put(try JSONEncoder().encode(latest.token),key:"recorded.latest.token")}
            // Legacy results already migrated to recorded_segments at open.
            try run("DELETE FROM kv WHERE key='recorded.latest' OR key LIKE 'recorded.result.%' OR key LIKE 'recorded.workout.%' OR key='bootstrap' OR key LIKE 'analysis.%' OR key LIKE 'sleep.%' OR key GLOB 'diary.[0-9]*h'")
            try run("DELETE FROM readings")
            let q=try statement("SELECT j.sequence,j.event_id,j.captured_at,j.kind,CASE WHEN j.kind IN ('ring_history','recorded_cardio') THEN j.payload ELSE NULL END,EXISTS(SELECT 1 FROM outbox o WHERE o.event_id=j.event_id) FROM journal j WHERE j.sequence>(SELECT value FROM retention_state WHERE name='legacy.cursor') AND j.sequence<=(SELECT value FROM retention_state WHERE name='legacy.end') ORDER BY j.sequence LIMIT ?")
            sqlite3_bind_int(q,1,Int32(max(1,min(limit,10000))));var rows:[(Int64,String,Double,String,Data?,Bool)]=[]
            while sqlite3_step(q)==SQLITE_ROW{rows.append((sqlite3_column_int64(q,0),text(q,1),sqlite3_column_double(q,2),text(q,3),sqlite3_column_type(q,4)==SQLITE_NULL ? nil:data(q,4),sqlite3_column_int(q,5)==1))};sqlite3_finalize(q)
            let remove=try statement("DELETE FROM journal WHERE event_id=? AND NOT EXISTS(SELECT 1 FROM outbox WHERE outbox.event_id=journal.event_id)");defer{sqlite3_finalize(remove)}
            for row in rows where !row.5{
                if let bytes=row.4{let payload=try JSONDecoder().decode(JSONValue.self,from:bytes);try markServerSaved(StoredEvent(event_id:row.1,sequence:row.0,captured_at:row.2,kind:row.3,payload:payload))}
                sqlite3_reset(remove);sqlite3_clear_bindings(remove);bind(row.1,remove,1);try done(remove)
            }
            if let last=rows.last{let update=try statement("UPDATE retention_state SET value=? WHERE name='legacy.cursor'");sqlite3_bind_int64(update,1,last.0);try done(update);sqlite3_finalize(update)}
            try pruneServerCopies()
            return rows.count<max(1,min(limit,10000))
        }
    }}
    func startRetentionMaintenance(diagnostic:DiagnosticWriter){
        let start=guarded{if retentionRunning{return false};retentionRunning=true;return true};guard start else{return}
        func step(){ioQueue.async{[weak self] in
            guard let self else{return}
            do{
                let complete=try self.retentionStep()
                if complete{try self.compactIfNeeded()}
                if complete || Date().timeIntervalSince1970-self.lastRetentionReport>=5{self.lastRetentionReport=Date().timeIntervalSince1970;diagnostic.submit(self.storageStatus(maintenanceComplete:complete))}
                if !complete{step()}else{self.guarded{self.retentionRunning=false}}
            }catch{diagnostic.submit(.object(["at":.number(Date().timeIntervalSince1970),"maintenance_complete":.bool(false),"cleanup_deferred":.bool(true)]));self.guarded{self.retentionRunning=false}}
        }}
        step()
    }
    func compactIfNeeded()throws{try guarded{
        let q=try statement("PRAGMA freelist_count");let free=sqlite3_step(q)==SQLITE_ROW ? sqlite3_column_int(q,0):0;sqlite3_finalize(q)
        if free>2048 && pendingCount==0{try run("PRAGMA wal_checkpoint(TRUNCATE)");try run("VACUUM");try run("PRAGMA wal_checkpoint(TRUNCATE)")}
    }}
    func storageStatus(maintenanceComplete:Bool)->JSONValue{guarded{
        var values:[String:JSONValue]=["at":.number(Date().timeIntervalSince1970),"maintenance_complete":.bool(maintenanceComplete),"pending_events":.number(Double(pendingCount)),"journal_events":.number(Double(journalCount))]
        for (name,table) in [("history_payload_rows","history_records"),("deduplication_hashes","history_receipts"),("model_result_rows","recorded_segments"),("sensor_reading_copies","readings")]{values[name] = .number(Double((try? count("SELECT count(*) FROM "+table)) ?? -1))}
        if let q=try? statement("PRAGMA database_list"),sqlite3_step(q)==SQLITE_ROW{let path=text(q,2);sqlite3_finalize(q);let size=(try? FileManager.default.attributesOfItem(atPath:path)[.size] as? NSNumber)?.doubleValue ?? 0;values["database_bytes"] = .number(size)}
        return .object(values)
    }}

}
