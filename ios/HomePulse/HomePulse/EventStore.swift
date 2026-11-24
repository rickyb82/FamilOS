import Foundation
import SQLite3
import CryptoKit

final class EventStore: @unchecked Sendable {
    private var db:OpaquePointer?
    private let lock=NSRecursiveLock()
    private let transient=unsafeBitCast(-1,to:sqlite3_destructor_type.self)
    init(url:URL) throws {
        try FileManager.default.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true,attributes:[.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication])
        guard sqlite3_open_v2(url.path,&db,SQLITE_OPEN_CREATE|SQLITE_OPEN_READWRITE|SQLITE_OPEN_FULLMUTEX,nil)==SQLITE_OK else {throw AppError.message("Archivio locale non disponibile")}
        try run("PRAGMA journal_mode=WAL");try run("PRAGMA synchronous=FULL");try run("PRAGMA foreign_keys=ON")
        try run("CREATE TABLE IF NOT EXISTS journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,captured_at REAL NOT NULL,kind TEXT NOT NULL,payload BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY REFERENCES journal(event_id),batch_id TEXT)")
        try run("CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY,body BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,body BLOB NOT NULL)")
        try run("CREATE TABLE IF NOT EXISTS history_records(digest TEXT PRIMARY KEY,ring TEXT,raw BLOB,received REAL,steps INTEGER,temp REAL,motion INTEGER)")
        try run("CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,bpm INTEGER NOT NULL)")
        try? FileManager.default.setAttributes([.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication],ofItemAtPath:url.path)
    }
    deinit {sqlite3_close(db)}
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
        let q=try statement("INSERT INTO outbox(event_id) VALUES(?)");defer{sqlite3_finalize(q)};bind(id,q,1);try done(q)
        return StoredEvent(event_id:id,sequence:seq,captured_at:at.timeIntervalSince1970,kind:kind,payload:payload)
    }
    func save<T:Encodable>(_ value:T,key:String) throws {try guarded{try put(try JSONEncoder().encode(value),key:key)}}
    private func put(_ data:Data,key:String) throws {let s=try statement("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)");defer{sqlite3_finalize(s)};bind(key,s,1);bind(data,s,2);try done(s)}
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
    func reconcileSessions(_ sessions:[WorkoutSession],active:WorkoutSession?)throws{
        try guarded{try transaction{
            guard try count("SELECT count(*) FROM outbox o JOIN journal j USING(event_id) WHERE j.kind='workout'")==0 else{return}
            for session in sessions{let s=try statement("INSERT OR REPLACE INTO sessions(id,body) VALUES(?,?)");defer{sqlite3_finalize(s)};bind(session.id.uuidString,s,1);bind(try JSONEncoder().encode(session),s,2);try done(s)}
            try put(try JSONEncoder().encode(active),key:"activeWorkout")
        }}
    }
    func sessions()->[WorkoutSession] {guarded{do{let s=try statement("SELECT body FROM sessions ORDER BY rowid DESC LIMIT 300");defer{sqlite3_finalize(s)};var a:[WorkoutSession]=[];while sqlite3_step(s)==SQLITE_ROW{if let v=try? JSONDecoder().decode(WorkoutSession.self,from:data(s,0)){a.append(v)}};return a.sorted{$0.startedAt>$1.startedAt}}catch{return []}}}
    func addReading(_ reading:Reading) throws {try guarded{let s=try statement("INSERT INTO readings(at,bpm) VALUES(?,?)");defer{sqlite3_finalize(s)};sqlite3_bind_double(s,1,reading.date.timeIntervalSince1970);sqlite3_bind_int(s,2,Int32(reading.bpm));try done(s)}}
    func readings(limit:Int=500)->[Reading] {guarded{do{let s=try statement("SELECT at,bpm FROM readings ORDER BY id DESC LIMIT ?");defer{sqlite3_finalize(s)};sqlite3_bind_int(s,1,Int32(limit));var a:[Reading]=[];while sqlite3_step(s)==SQLITE_ROW{a.append(Reading(date:Date(timeIntervalSince1970:sqlite3_column_double(s,0)),bpm:Int(sqlite3_column_int(s,1))))};return a.reversed()}catch{return []}}}
    var pendingCount:Int {guarded{(try? count("SELECT count(*) FROM outbox")) ?? 0}}
    var journalCount:Int {guarded{(try? count("SELECT count(*) FROM journal")) ?? 0}}
    private func count(_ sql:String) throws->Int {let s=try statement(sql);defer{sqlite3_finalize(s)};return sqlite3_step(s)==SQLITE_ROW ? Int(sqlite3_column_int(s,0)):0}
    func prepareBatch(limit:Int=100) throws->EventBatch? {try guarded{try transaction{
        let old=try statement("SELECT body FROM batches ORDER BY rowid LIMIT 1");defer{sqlite3_finalize(old)}
        if sqlite3_step(old)==SQLITE_ROW{return try JSONDecoder().decode(EventBatch.self,from:data(old,0))}
        let s=try statement("SELECT j.event_id,j.sequence,j.captured_at,j.kind,j.payload FROM journal j JOIN outbox o USING(event_id) WHERE o.batch_id IS NULL ORDER BY j.sequence LIMIT ?");defer{sqlite3_finalize(s)};sqlite3_bind_int(s,1,Int32(max(1,min(500,limit))))
        var events:[StoredEvent]=[]
        while sqlite3_step(s)==SQLITE_ROW {events.append(StoredEvent(event_id:text(s,0),sequence:sqlite3_column_int64(s,1),captured_at:sqlite3_column_double(s,2),kind:text(s,3),payload:try JSONDecoder().decode(JSONValue.self,from:data(s,4))))}
        guard !events.isEmpty else{return nil};let batch=EventBatch(batch_id:UUID().uuidString,events:events)
        let b=try statement("INSERT INTO batches(batch_id,body) VALUES(?,?)");defer{sqlite3_finalize(b)};bind(batch.batch_id,b,1);bind(try JSONEncoder().encode(batch),b,2);try done(b)
        for e in events {let q=try statement("UPDATE outbox SET batch_id=? WHERE event_id=?");bind(batch.batch_id,q,1);bind(e.event_id,q,2);try done(q);sqlite3_finalize(q)}
        return batch
    }}}
    func acknowledge(batchID:String,accepted:[String]) throws {try guarded{try transaction{
        let s=try statement("SELECT body FROM batches WHERE batch_id=?");defer{sqlite3_finalize(s)};bind(batchID,s,1)
        guard sqlite3_step(s)==SQLITE_ROW else{return}
        let batch=try JSONDecoder().decode(EventBatch.self,from:data(s,0));let expected=Set(batch.events.map{$0.event_id})
        guard Set(accepted).isSubset(of:expected) else {throw AppError.message("Conferma server incoerente: dati locali conservati")}
        for id in Set(accepted) {let q=try statement("DELETE FROM outbox WHERE event_id=? AND batch_id=?");bind(id,q,1);bind(batchID,q,2);try done(q);sqlite3_finalize(q)}
        let q=try statement("UPDATE outbox SET batch_id=NULL WHERE batch_id=?");defer{sqlite3_finalize(q)};bind(batchID,q,1);try done(q)
        let b=try statement("DELETE FROM batches WHERE batch_id=?");defer{sqlite3_finalize(b)};bind(batchID,b,1);try done(b)
    }}}
    func archiveHistory(frames:[Data],nextCursor:UInt32,key:String,api:[Int],ring:String?) throws {try guarded{try transaction{
        for frame in frames {
            let bytes=[UInt8](frame)
            let digest=Data(SHA256.hash(data:Data((ring ?? "unknown").utf8)+frame)).hex
            let h=try statement("INSERT OR IGNORE INTO history_records(digest,ring,raw,received,steps,temp,motion) VALUES(?,?,?,?,?,?,?)")
            bind(digest,h,1);bind(ring ?? "unknown",h,2);bind(frame,h,3);sqlite3_bind_double(h,4,Date().timeIntervalSince1970)
            if bytes.count>=7,bytes[0]==0x50 {sqlite3_bind_int(h,5,Int32(bytes[6]))}else{sqlite3_bind_null(h,5)}
            if (10...20).contains(bytes.count),bytes[0]==0x46,bytes.count%2==0 {let value=Int16(bitPattern:UInt16(bytes[6])|(UInt16(bytes[7])<<8));if value != Int16.min{sqlite3_bind_double(h,6,Double(value)/100)}else{sqlite3_bind_null(h,6)}}else{sqlite3_bind_null(h,6)}
            if (10...12).contains(bytes.count),bytes[0]==0x47 {sqlite3_bind_int(h,7,Int32(bytes[6]&31))}else{sqlite3_bind_null(h,7)}
            try done(h);let inserted=sqlite3_changes(db)>0;sqlite3_finalize(h)
            if !inserted {continue}
            var p:[String:JSONValue]=["raw_hex":.string(frame.hex),"api_version":.array(api.map{.number(Double($0))})]
            if let ring {p["logical_ring_id"] = .string(ring)}
            var identity=[UInt8](Data(SHA256.hash(data:Data(digest.utf8))).prefix(16));identity[6]=(identity[6]&15)|0x50;identity[8]=(identity[8]&63)|0x80
            let eventID=UUID(uuidString:NSUUID(uuidBytes:identity).uuidString)!
            _=try insert(kind:"ring_history",payload:.object(p),at:Date(),eventID:eventID)
        }
        try put(try JSONEncoder().encode(nextCursor),key:key)
    }}}
    func metrics()->HistoricalMetrics {guarded{do{
        var result=HistoricalMetrics()
        let a=try statement("SELECT temp,received FROM history_records WHERE temp IS NOT NULL ORDER BY rowid DESC LIMIT 1");defer{sqlite3_finalize(a)}
        if sqlite3_step(a)==SQLITE_ROW{result.temperatureC=sqlite3_column_double(a,0);result.receivedAt=Date(timeIntervalSince1970:sqlite3_column_double(a,1))}
        let b=try statement("SELECT sum(steps),count(steps) FROM history_records");defer{sqlite3_finalize(b)}
        if sqlite3_step(b)==SQLITE_ROW,sqlite3_column_int(b,1)>0{result.steps=Int(sqlite3_column_int64(b,0))}
        let c=try statement("SELECT motion FROM history_records WHERE motion IS NOT NULL ORDER BY rowid DESC LIMIT 1");defer{sqlite3_finalize(c)}
        if sqlite3_step(c)==SQLITE_ROW{result.motionSeconds=Int(sqlite3_column_int(c,0))};return result
    }catch{return HistoricalMetrics()}}}

}
