import XCTest
import SQLite3
@testable import HomePulse

final class HomePulseTests:XCTestCase {
    func testRejectedBluetoothAttemptsHaveBoundedBackoff(){
        XCTAssertEqual((1...7).map{RingReconnectPolicy.delay(consecutiveFailures:$0)},[5,10,20,30,60,60,60])
        XCTAssertEqual(RingReconnectPolicy.delay(consecutiveFailures:0),5)
        XCTAssertEqual(RingReconnectPolicy.delay(consecutiveFailures:Int.max),60)
    }
    func testBlockedDiagnosticWriteDoesNotBlockMainAndCoalescesPendingSnapshots()async throws{
        let entered=expectation(description:"diagnostic sink entered"),completed=expectation(description:"latest snapshot written")
        let gate=DispatchSemaphore(value:0),captures=SnapshotWrites()
        defer{gate.signal()}
        let writer=DiagnosticWriter(url:URL(fileURLWithPath:"/synthetic-unused.json"),minimumInterval:0){data,_ in
            XCTAssertFalse(Thread.isMainThread)
            let value=try JSONDecoder().decode(JSONValue.self,from:data);captures.append(value)
            if value["status"].string=="first"{entered.fulfill();XCTAssertEqual(gate.wait(timeout:.now()+5),.success)}
            else{completed.fulfill()}
        }
        await MainActor.run{writer.submit(.object(["status":.string("first")]))}
        await fulfillment(of:[entered],timeout:3)
        let start=Date()
        await MainActor.run{
            writer.submit(.object(["status":.string("middle")]))
            writer.submit(.object(["status":.string("latest")]))
        }
        XCTAssertLessThan(Date().timeIntervalSince(start),0.5,"Main actor must not wait for the blocked file write")
        gate.signal();await fulfillment(of:[completed],timeout:3)
        XCTAssertEqual(captures.values.map{$0["status"].string},["first","latest"])
    }
    func testProtectedReplacementKeepsCompleteFileAndRejectsPendingSymlink()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        defer{try? FileManager.default.removeItem(at:folder)}
        let file=folder.appendingPathComponent("snapshot.json"),pending=file.appendingPathExtension("pending"),other=folder.appendingPathComponent("other.json")
        try ProtectedFileIO.replace(Data("first".utf8),at:file)
        try ProtectedFileIO.replace(Data("complete replacement".utf8),at:file)
        XCTAssertEqual(try Data(contentsOf:file),Data("complete replacement".utf8));XCTAssertFalse(FileManager.default.fileExists(atPath:pending.path))
        let attrs=try FileManager.default.attributesOfItem(atPath:file.path)
        XCTAssertEqual((attrs[.posixPermissions] as? NSNumber)?.intValue,0o600)
        try ProtectedFileIO.replace(Data("untouched".utf8),at:other)
        try FileManager.default.createSymbolicLink(at:pending,withDestinationURL:other)
        XCTAssertThrowsError(try ProtectedFileIO.replace(Data("rejected".utf8),at:file))
        XCTAssertEqual(try Data(contentsOf:other),Data("untouched".utf8));XCTAssertEqual(try Data(contentsOf:file),Data("complete replacement".utf8))
    }
    func testUnchangedKeyValueSnapshotDoesNotCommitAnotherDatabaseWrite()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let url=folder.appendingPathComponent("snapshots.sqlite"),store=try EventStore(url:url)
        try store.save(["b":2,"a":1],key:"technical-state")
        var reader:OpaquePointer?;XCTAssertEqual(sqlite3_open(url.path,&reader),SQLITE_OK);defer{sqlite3_close(reader)}
        func version()throws->Int32{
            var statement:OpaquePointer?;XCTAssertEqual(sqlite3_prepare_v2(reader,"PRAGMA data_version",-1,&statement,nil),SQLITE_OK)
            defer{sqlite3_finalize(statement)};XCTAssertEqual(sqlite3_step(statement),SQLITE_ROW);return sqlite3_column_int(statement,0)
        }
        let before=try version();try store.save(["a":1,"b":2],key:"technical-state");XCTAssertEqual(try version(),before)
        try store.save(["a":3,"b":2],key:"technical-state");XCTAssertNotEqual(try version(),before)
        XCTAssertEqual(store.load([String:Int].self,key:"technical-state"),["a":3,"b":2])
    }
    func testSpO2AutomaticProtocolMatchesOriginalGen3AndKeepsRawResultSeparateFromMode()throws{
        XCTAssertEqual(OuraWire.spo2AutomaticParameters(capabilityVersion:1)?.hex,"2f0a29040164031e18000000")
        XCTAssertNil(OuraWire.spo2AutomaticParameters(capabilityVersion:3))
        let automatic=try OuraWire.featureStatus(Data(hex:"2f06210401000200")!,feature:4)
        XCTAssertEqual(automatic.mode,1);XCTAssertEqual(automatic.state,2);XCTAssertEqual(automatic.result,0)
        let resultLowBattery=try OuraWire.featureStatus(Data(hex:"2f06210401000005")!,feature:4)
        XCTAssertEqual(resultLowBattery.mode,1);XCTAssertEqual(resultLowBattery.result,5)
        XCTAssertNoThrow(try OuraWire.ack(Data(hex:"2f032a0400")!,extended:0x2a,feature:4))
        XCTAssertThrowsError(try OuraWire.ack(Data(hex:"2f032a0401")!,extended:0x2a,feature:4))
        XCTAssertThrowsError(try OuraWire.featureStatus(Data(hex:"2f06210301000200")!,feature:4))
        XCTAssertThrowsError(try OuraWire.featureStatus(Data(hex:"2f052104010002")!,feature:4))
    }
    func testDisplayReadingsBoundChartWorkWithoutInventingOrLosingExtrema(){
        var source=(0..<5000).map{Reading(date:Date(timeIntervalSince1970:Double($0)),bpm:80)}
        source[1234].bpm=240;source[3456].bpm=40
        let shown=AppModel.displayReadings(source),ids=Set(source.map{$0.id})
        XCTAssertLessThanOrEqual(shown.count,150);XCTAssertTrue(shown.allSatisfy{ids.contains($0.id)})
        XCTAssertEqual(shown.first?.id,source.first?.id);XCTAssertEqual(shown.last?.id,source.last?.id)
        XCTAssertEqual(shown.map{$0.bpm}.max(),240);XCTAssertEqual(shown.map{$0.bpm}.min(),40)
        XCTAssertEqual(source.count,5000)
    }
    func testLegacyOutboxMigrationKeepsUnacknowledgedBatchAndFIFO()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let url=folder.appendingPathComponent("legacy.sqlite")
        var db:OpaquePointer?;XCTAssertEqual(sqlite3_open(url.path,&db),SQLITE_OK)
        let payload:JSONValue = .object(["raw_hex":.string("410401000000")])
        let events=(1...3).map{StoredEvent(event_id:UUID().uuidString,sequence:Int64($0),captured_at:Double(4-$0),kind:"ring_history",payload:payload)}
        let batch=EventBatch(batch_id:UUID().uuidString,events:[events[0]])
        func literal(_ bytes:Data)->String{"X'"+bytes.map{String(format:"%02x",$0)}.joined()+"'"}
        var sql="CREATE TABLE journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,captured_at REAL NOT NULL,kind TEXT NOT NULL,payload BLOB NOT NULL);CREATE TABLE outbox(event_id TEXT PRIMARY KEY REFERENCES journal(event_id),batch_id TEXT);CREATE TABLE batches(batch_id TEXT PRIMARY KEY,body BLOB NOT NULL);"
        for event in events{sql+="INSERT INTO journal VALUES(\(event.sequence),'\(event.event_id)',\(event.captured_at),'ring_history',\(literal(try JSONEncoder().encode(payload))));INSERT INTO outbox VALUES('\(event.event_id)',\(event.sequence==1 ? "'\(batch.batch_id)'":"NULL"));"}
        sql+="INSERT INTO batches VALUES('\(batch.batch_id)',\(literal(try JSONEncoder().encode(batch))));"
        XCTAssertEqual(sqlite3_exec(db,sql,nil,nil,nil),SQLITE_OK);sqlite3_close(db)
        let migrated=try EventStore(url:url)
        XCTAssertEqual(migrated.pendingCount,3);XCTAssertEqual(migrated.journalCount,3)
        let replay=try XCTUnwrap(migrated.prepareBatch(limit:500,prioritizeLive:true))
        XCTAssertEqual(replay.batch_id,batch.batch_id);XCTAssertEqual(replay.events.map{$0.event_id},[events[0].event_id])
        try migrated.acknowledge(batchID:batch.batch_id,accepted:[events[0].event_id])
        XCTAssertEqual(try migrated.prepareBatch()?.events.map{$0.event_id},Array(events.dropFirst().map{$0.event_id}))
    }
    func testPacketJournalPersistsOrderedFramesOffMainAndDeduplicatesHistory()async throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("packets.sqlite")),journal=PacketJournal(store:store)
        let history=Data(hex:"50050100000005")!,live=Data(hex:"2f0f280211020000e81300000000ac0d01")!,secret=Data(hex:"24020102")!
        let first=Date(timeIntervalSince1970:1000),second=first.addingTimeInterval(1)
        let done=expectation(description:"serial persisted deliveries");done.expectedFulfillmentCount=2
        journal.ingest(history+live+secret,at:first,api:[2,0,0],ring:"synthetic-ring"){delivery in
            XCTAssertFalse(Thread.isMainThread);XCTAssertFalse(delivery.storageFailed);XCTAssertEqual(delivery.at,first);XCTAssertEqual(delivery.frames,[history,live,secret]);done.fulfill()
        }
        journal.ingest(history,at:second,api:[2,0,0],ring:"synthetic-ring"){delivery in
            XCTAssertFalse(Thread.isMainThread);XCTAssertFalse(delivery.storageFailed);XCTAssertEqual(delivery.at,second);done.fulfill()
        }
        await fulfillment(of:[done],timeout:5)
        XCTAssertEqual(store.pendingCount,2);XCTAssertNil(store.load(UInt32.self,key:"cursor"))
        let batch=try XCTUnwrap(store.prepareBatch())
        XCTAssertEqual(batch.events.map{$0.kind},["ring_history","sensor_packet"])
        XCTAssertEqual(batch.events.map{$0.captured_at},[first.timeIntervalSince1970,first.timeIntervalSince1970])
        XCTAssertEqual(batch.events.map{$0.payload["raw_hex"].string},[history.hex,live.hex])
        try store.archiveHistory(frames:[history],nextCursor:2,key:"cursor",api:[2,0,0],ring:"synthetic-ring")
        XCTAssertEqual(store.pendingCount,2);XCTAssertEqual(store.metrics().steps,5);XCTAssertEqual(store.load(UInt32.self,key:"cursor"),2)
    }
    func testWiFiDrainsSuccessiveFullBatchesWithoutNewSensorCallbacks()async throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("wifi.sqlite"))
        try store.ingestPackets(Array(repeating:Data(hex:"0d03320000")!,count:2001),api:[2,0,0],ring:nil,at:Date())
        let done=expectation(description:"all Wi-Fi ACKs");done.expectedFulfillmentCount=3
        let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[HomePulseHTTPFixture.self]
        let session=URLSession(configuration:config);defer{session.invalidateAndCancel();HomePulseHTTPFixture.reply=nil}
        let lock=NSLock();var counts:[Int]=[];var seen=Set<String>()
        HomePulseHTTPFixture.reply={request in
            var body=request.httpBody
            if body==nil,let stream=request.httpBodyStream{stream.open();defer{stream.close()};var data=Data(),buffer=[UInt8](repeating:0,count:4096);while stream.hasBytesAvailable{let n=stream.read(&buffer,maxLength:buffer.count);if n<=0{break};data.append(contentsOf:buffer.prefix(n))};body=data}
            let batch=try JSONDecoder().decode(EventBatch.self,from:XCTUnwrap(body))
            lock.lock();counts.append(batch.events.count);for event in batch.events{XCTAssertTrue(seen.insert(event.event_id).inserted)};lock.unlock()
            return try JSONEncoder().encode(JSONValue.object(["batch_id":.string(batch.batch_id),"accepted_event_ids":.array(batch.events.map{.string($0.event_id)})]))
        }
        let credential=Credentials(device_id:"synthetic-device",access_token:"synthetic-only",server:URL(string:"https://synthetic.example/api/wellness/v1")!)
        let api=WellnessAPI(store:store,folder:folder.appendingPathComponent("uploads"),transport:session,credentials:{credential},monitorNetwork:false,fastNetwork:true)
        api.onAcknowledged={done.fulfill()};api.setForeground(true)
        await fulfillment(of:[done],timeout:5)
        await api.stopForTesting()
        XCTAssertEqual(counts,[1000,1000,1]);XCTAssertEqual(seen.count,2001);XCTAssertEqual(store.pendingCount,0);XCTAssertEqual(store.journalCount,2001)
    }
    func testSleepPreservesNativeSummaryAndPartialRelativeSequence(){
        let period=SleepPeriod(.object(["id":.string("synthetic-sleep"),"started_at":.number(1000),"ended_at":.number(4600),"time_in_bed_seconds":.number(3600),"total_sleep_seconds":.number(3000),"stage_seconds":.object(["deep":.number(600)]),"phases_30_seconds":.string("11234")]))
        XCTAssertEqual(period?.total,3000);XCTAssertEqual(period?.stages["deep"],600)
        XCTAssertEqual(period?.phaseRuns.count,4);XCTAssertEqual(period?.phaseRuns.last?.end,150)
        XCTAssertEqual(period?.phaseRuns.first?.stage,"Profondo");XCTAssertNil(period?.efficiency)
        XCTAssertNil(SleepPeriod(.object(["id":.string("invalid")])))
    }
    func testLargeBacklogSelectsLiveWithoutSortingPayloadsAndReplaysExactBatch()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let url=folder.appendingPathComponent("backlog.sqlite"),store=try EventStore(url:url)
        var db:OpaquePointer?;XCTAssertEqual(sqlite3_open(url.path,&db),SQLITE_OK)
        defer{sqlite3_close(db)}
        XCTAssertEqual(sqlite3_exec(db,"BEGIN",nil,nil,nil),SQLITE_OK)
        var insert:OpaquePointer?,outbox:OpaquePointer?
        XCTAssertEqual(sqlite3_prepare_v2(db,"INSERT INTO journal(event_id,captured_at,kind,payload) VALUES(?1,1,'ring_history',?2)",-1,&insert,nil),SQLITE_OK)
        XCTAssertEqual(sqlite3_prepare_v2(db,"INSERT INTO outbox(event_id) VALUES(?1)",-1,&outbox,nil),SQLITE_OK)
        defer{sqlite3_finalize(insert);sqlite3_finalize(outbox)}
        let transient=unsafeBitCast(-1,to:sqlite3_destructor_type.self)
        let payload=try JSONEncoder().encode(JSONValue.object(["raw_hex":.string(String(repeating:"ab",count:4096))]))
        var ids:[String]=[]
        for _ in 0..<2500{
            let id=UUID().uuidString;ids.append(id);sqlite3_reset(insert);sqlite3_reset(outbox)
            sqlite3_bind_text(insert,1,id,-1,transient)
            _=payload.withUnsafeBytes{sqlite3_bind_blob(insert,2,$0.baseAddress,Int32(payload.count),transient)}
            sqlite3_bind_text(outbox,1,id,-1,transient)
            XCTAssertEqual(sqlite3_step(insert),SQLITE_DONE);XCTAssertEqual(sqlite3_step(outbox),SQLITE_DONE)
        }
        XCTAssertEqual(sqlite3_exec(db,"COMMIT",nil,nil,nil),SQLITE_OK)
        let live=try store.append(kind:"sensor_packet",payload:.object(["raw_hex":.string("2f0f280211020000e81300000000ac0d01")]))
        let started=Date(),batch=try XCTUnwrap(store.prepareBatch(limit:128,prioritizeLive:true))
        XCTAssertLessThan(Date().timeIntervalSince(started),5,"Backlog preparation must stay below the iOS scene watchdog budget")
        XCTAssertEqual(batch.events.count,128);XCTAssertEqual(batch.events.first?.event_id,live.event_id)
        XCTAssertEqual(batch.events.dropFirst().map{$0.event_id},Array(ids.prefix(127)))
        let encoder=JSONEncoder();encoder.outputFormatting = .sortedKeys
        XCTAssertEqual(try encoder.encode(try store.prepareBatch()),try encoder.encode(Optional(batch)))
        XCTAssertTrue(try store.acknowledge(batchID:batch.batch_id,accepted:batch.events.map{$0.event_id}))
        XCTAssertEqual(store.pendingCount,2373);XCTAssertEqual(store.journalCount,2501)
        let next=try XCTUnwrap(store.prepareBatch(limit:1))
        XCTAssertEqual(next.events.first?.event_id,ids[127])
        XCTAssertEqual(next.events.first?.payload["raw_hex"].string,String(repeating:"ab",count:4096))
    }

    func testForegroundSendsExactDurableBatchAndAcknowledgesIt()async throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("network.sqlite"))
        let event=try store.append(kind:"sensor_packet",payload:.object(["raw_hex":.string("2f0f280211020000e81300000000ac0d01")]))
        let done=expectation(description:"immediate request ACK")
        let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[HomePulseHTTPFixture.self]
        let session=URLSession(configuration:config)
        defer{session.invalidateAndCancel();HomePulseHTTPFixture.reply=nil}
        HomePulseHTTPFixture.reply={request in
            XCTAssertEqual(request.url?.path,"/api/wellness/v1/bridges/events")
            XCTAssertEqual(request.httpMethod,"POST");XCTAssertEqual(request.value(forHTTPHeaderField:"Authorization"),"Bearer synthetic-only")
            var body=request.httpBody
            if body==nil,let stream=request.httpBodyStream{stream.open();defer{stream.close()};var data=Data();var buffer=[UInt8](repeating:0,count:4096);while stream.hasBytesAvailable{let count=stream.read(&buffer,maxLength:buffer.count);if count<=0{break};data.append(contentsOf:buffer.prefix(count))};body=data}
            let batch=try JSONDecoder().decode(EventBatch.self,from:XCTUnwrap(body))
            XCTAssertEqual(batch.events.map{$0.event_id},[event.event_id]);XCTAssertEqual(batch.events[0].captured_at,event.captured_at)
            return try JSONEncoder().encode(JSONValue.object(["batch_id":.string(batch.batch_id),"accepted_event_ids":.array([.string(event.event_id)])]))
        }
        let credentials=Credentials(device_id:"synthetic-device",access_token:"synthetic-only",server:URL(string:"https://synthetic.example/api/wellness/v1")!)
        let api=WellnessAPI(store:store,folder:folder.appendingPathComponent("uploads"),transport:session,credentials:{credentials},monitorNetwork:false)
        api.onAcknowledged={done.fulfill()};api.setForeground(true)
        await fulfillment(of:[done],timeout:5)
        await api.stopForTesting()
        XCTAssertEqual(store.pendingCount,0);XCTAssertEqual(store.journalCount,1)
    }

    func testBluetoothWakeSendsExactBatchWithoutForeground()async throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("network.sqlite"))
        let event=try store.append(kind:"sensor_packet",payload:.object(["raw_hex":.string("2f0f280211020000e81300000000ac0d01")]))
        let done=expectation(description:"immediate request ACK")
        let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[HomePulseHTTPFixture.self]
        let session=URLSession(configuration:config)
        defer{session.invalidateAndCancel();HomePulseHTTPFixture.reply=nil}
        HomePulseHTTPFixture.reply={request in
            XCTAssertEqual(request.url?.path,"/api/wellness/v1/bridges/events")
            XCTAssertEqual(request.httpMethod,"POST");XCTAssertEqual(request.value(forHTTPHeaderField:"Authorization"),"Bearer synthetic-only")
            var body=request.httpBody
            if body==nil,let stream=request.httpBodyStream{stream.open();defer{stream.close()};var data=Data();var buffer=[UInt8](repeating:0,count:4096);while stream.hasBytesAvailable{let count=stream.read(&buffer,maxLength:buffer.count);if count<=0{break};data.append(contentsOf:buffer.prefix(count))};body=data}
            let batch=try JSONDecoder().decode(EventBatch.self,from:XCTUnwrap(body))
            XCTAssertEqual(batch.events.map{$0.event_id},[event.event_id]);XCTAssertEqual(batch.events[0].captured_at,event.captured_at)
            return try JSONEncoder().encode(JSONValue.object(["batch_id":.string(batch.batch_id),"accepted_event_ids":.array([.string(event.event_id)])]))
        }
        let credentials=Credentials(device_id:"synthetic-device",access_token:"synthetic-only",server:URL(string:"https://synthetic.example/api/wellness/v1")!)
        let api=WellnessAPI(store:store,folder:folder.appendingPathComponent("uploads"),transport:session,credentials:{credentials},monitorNetwork:false)
        api.onAcknowledged={done.fulfill()};api.noteBluetoothActivity()
        await fulfillment(of:[done],timeout:5)
        await api.stopForTesting()
        XCTAssertEqual(store.pendingCount,0);XCTAssertEqual(store.journalCount,1)
    }

    func testImmediateBatchPrioritizesLiveAndPreservesHistoryForReplay()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("live.sqlite"))
        let old=try store.append(kind:"ring_history",payload:.object(["raw_hex":.string("410401000000")]),at:Date().addingTimeInterval(-60))
        let live=try store.append(kind:"sensor_packet",payload:.object(["raw_hex":.string("2f0f280211020000e81300000000ac0d01")]))
        let first=try XCTUnwrap(store.prepareBatch(limit:1,prioritizeLive:true))
        XCTAssertEqual(first.events.map{$0.event_id},[live.event_id]);XCTAssertEqual(first.events[0].sequence,live.sequence)
        XCTAssertEqual(try store.prepareBatch(prioritizeLive:true)?.batch_id,first.batch_id)
        try store.acknowledge(batchID:first.batch_id,accepted:[live.event_id])
        let remaining=try XCTUnwrap(store.prepareBatch(prioritizeLive:true))
        XCTAssertEqual(remaining.events.map{$0.event_id},[old.event_id]);XCTAssertEqual(store.journalCount,2)
    }

    func testDisplayRetainsActualHeartValueAcrossMotionInvalidPacketsAndSilence()throws{
        let good=Data(hex:"2f0f280211020000e81300000000ac0d01")!,start=Date(timeIntervalSince1970:1000)
        var display=HeartDisplayState()
        var invalid=good;invalid[9]=3
        XCTAssertFalse(display.receive(try OuraWire.live(invalid,api:[2,0,0]),at:start))
        XCTAssertNil(display.bpm);XCTAssertNil(display.capturedAt)
        XCTAssertTrue(display.receive(try OuraWire.live(good,api:[2,0,0]),at:start))
        XCTAssertTrue(display.isFresh(at:start));XCTAssertEqual(display.bpm,60)
        XCTAssertFalse(display.receive(try OuraWire.live(invalid,api:[2,0,0]),at:start.addingTimeInterval(1)))
        XCTAssertEqual(display.bpm,60);XCTAssertEqual(display.capturedAt,start)
        XCTAssertFalse(display.isFresh(at:start.addingTimeInterval(1)))
        XCTAssertEqual(display.ageSeconds(at:start.addingTimeInterval(30)),30)
        XCTAssertEqual(display.bpm,60);XCTAssertFalse(display.isFresh(at:start.addingTimeInterval(30)))
        XCTAssertTrue(display.receive(try OuraWire.live(good,api:[2,0,0]),at:start.addingTimeInterval(31)))
        XCTAssertTrue(display.isFresh(at:start.addingTimeInterval(31)))
        XCTAssertFalse(display.isFresh(at:start.addingTimeInterval(40)))
        XCTAssertEqual(display.bpm,60);XCTAssertEqual(display.flags,17)
        var outside=try OuraWire.live(good,api:[2,0,0]);outside.bpm=600
        XCTAssertFalse(display.receive(outside,at:start.addingTimeInterval(41)))
        XCTAssertEqual(display.bpm,60);XCTAssertEqual(display.capturedAt,start.addingTimeInterval(31))
    }

    func testBatteryAlertThresholdHysteresisAndPersistence()throws{
        var alert=BatteryAlertState()
        XCTAssertFalse(alert.observe(11));XCTAssertTrue(alert.observe(10));XCTAssertFalse(alert.observe(9))
        alert=try JSONDecoder().decode(BatteryAlertState.self,from:JSONEncoder().encode(alert))
        XCTAssertFalse(alert.observe(10));XCTAssertFalse(alert.observe(11));XCTAssertFalse(alert.observe(10))
        XCTAssertFalse(alert.observe(20));XCTAssertTrue(alert.observe(8));XCTAssertFalse(alert.observe(0))
        XCTAssertFalse(alert.observe(101));XCTAssertFalse(alert.observe(-1))
        var first=BatteryAlertState();XCTAssertTrue(first.observe(5))
    }

    func testChargingWakeOnlyRunsOffscreenOnChargerAndWhenIdle(){
        func delay(_ foreground:Bool=false,_ desired:Bool=true,_ ready:Bool=true,_ busy:Bool=false,_ progress:Int?=53)->TimeInterval?{
            ChargeCheckPolicy.delay(foreground:foreground,desired:desired,ready:ready,busy:busy,progress:progress)
        }
        XCTAssertEqual(delay(),60);XCTAssertEqual(delay(false,true,true,false,100),60)
        XCTAssertNil(delay(true));XCTAssertNil(delay(false,false));XCTAssertNil(delay(false,true,false))
        XCTAssertNil(delay(false,true,true,true));XCTAssertNil(delay(false,true,true,false,0))
        XCTAssertNil(delay(false,true,true,false,nil));XCTAssertNil(delay(false,true,true,false,255))
    }

    func testFullBatteryAlertOnceAcrossRestartAndChargeFluctuation()throws{
        var alert=BatteryFullAlertState()
        XCTAssertFalse(alert.observe(level:99,progress:99))
        XCTAssertTrue(alert.observe(level:100,progress:100))
        alert=try JSONDecoder().decode(BatteryFullAlertState.self,from:JSONEncoder().encode(alert))
        XCTAssertFalse(alert.observe(level:100,progress:100))
        XCTAssertFalse(alert.observe(level:99,progress:99))
        XCTAssertFalse(alert.observe(level:100,progress:100))
        XCTAssertFalse(alert.observe(level:99,progress:0))
        XCTAssertTrue(alert.observe(level:100,progress:100))
    }
    func testFullBatteryAlertNeedsFirmwareCompletionAndRearmsAfterOfflineDischarge(){
        var alert=BatteryFullAlertState()
        XCTAssertFalse(alert.observe(level:100,progress:nil))
        XCTAssertFalse(alert.observe(level:100,progress:255))
        XCTAssertFalse(alert.observe(level:101,progress:100))
        XCTAssertFalse(alert.observe(level:100,progress:0))
        XCTAssertTrue(alert.observe(level:100,progress:100))
        XCTAssertFalse(alert.observe(level:50,progress:50))
        XCTAssertTrue(alert.observe(level:100,progress:100))
    }

    func testLiveIBIWithMotionRemainsAcceptedAndFlagged()throws{
        let d=Data(hex:"2f0f280211020000e81300000000ac0d01")!
        let value=try OuraWire.live(d,api:[2,0,0])
        XCTAssertEqual(value.bpm,60);XCTAssertEqual(value.ibi,1000)
        XCTAssertTrue(value.warnings.contains("Movimento segnalato"));XCTAssertEqual(value.temperatureRaw,3500)
        var invalid=d;invalid[9]=3
        XCTAssertNil(try OuraWire.live(invalid,api:[2,0,0]).bpm)
    }
    func testAESMatchesSyntheticAndroidKnownAnswer()throws{
        let key=Data(hex:"000102030405060708090a0b0c0d0e0f")!
        let nonce=Data(hex:"000102030405060708090a0b0c0d0e")!
        XCTAssertEqual(try OuraWire.cipher(key:key,nonce:nonce).hex,"b61e6af8da7260d2214369b951bf8963")
        XCTAssertThrowsError(try OuraWire.cipher(key:Data(),nonce:nonce))
    }
    func testFramingHandlesFragmentsAndConcatenatedRecords()throws{
        var framer=OuraFramer()
        let a=Data(hex:"410401000000")!,b=Data(hex:"43050200000061")!
        XCTAssertTrue(try framer.consume(a.prefix(3)).isEmpty)
        XCTAssertEqual(try framer.consume(Data(a.dropFirst(3))+b),[a,b])
        XCTAssertEqual(try OuraWire.cursor(b),2)
        XCTAssertEqual(OuraWire.historyRequest(cursor:18645).hex,"1009d548000040ffffffff")
        XCTAssertEqual(OuraWire.historyRequest(cursor:100,max:255).hex,"100964000000ffffffffff")
    }
    func testCredentialsAndNonceExcludedFromJournalPolicy(){
        XCTAssertTrue(OuraWire.sensitive(Data(hex:"2f102c000102030405060708090a0b0c0d0e")!))
        XCTAssertTrue(OuraWire.sensitive(Data(hex:"2f022e00")!))
        XCTAssertFalse(OuraWire.sensitive(Data(hex:"2f0e2504000100000000000000000000")!))
    }
    func testSportProtocolRequiresProfileAndMatchesFiveBytePayload()throws{
        XCTAssertEqual(try OuraWire.sportParameters(activity:5,age:40,resting:60).hex,"2f07290305003c32be")
        XCTAssertThrowsError(try OuraWire.sportParameters(activity:5,age:0,resting:60))
    }
    func testSportRejectionPreservesUnknownResultAndTheFailedOperation()throws{
        let frame=Data(hex:"2f032a0320")!
        XCTAssertThrowsError(try OuraWire.ack(frame,extended:0x2a,feature:3,operation:"Parametri cardio attività")){error in
            let rejection=error as? OuraCommandRejection
            XCTAssertEqual(rejection?.result,32);XCTAssertEqual(rejection?.command,0x2a);XCTAssertEqual(rejection?.feature,3)
            XCTAssertEqual(error.localizedDescription,"Parametri cardio attività: Comando anello rifiutato (esito 32)")
        }
        XCTAssertThrowsError(try OuraWire.ack(Data(hex:"2f03230320")!,extended:0x23,feature:2)){error in
            XCTAssertNil(error as? OuraCommandRejection,"A reply for another feature must not be diagnosed as this command's rejection")
        }
    }
    func testModeWritesSkipAlreadyAppliedModesWithoutTreatingStatusResultAsAnAck()throws{
        XCTAssertFalse(try OuraWire.modeChangeNeeded(Data(hex:"2f06210203200220")!,feature:2,desired:3))
        XCTAssertFalse(try OuraWire.modeChangeNeeded(Data(hex:"2f06210300000020")!,feature:3,desired:0))
        XCTAssertTrue(try OuraWire.modeChangeNeeded(Data(hex:"2f06210301000220")!,feature:3,desired:0))
        XCTAssertThrowsError(try OuraWire.modeChangeNeeded(Data(hex:"2f06210203200220")!,feature:3,desired:0))
    }
    func testBatteryTicksCannotPostponeExerciseStatusAndManualReadsRemainBounded(){
        let start=Date(timeIntervalSince1970:1000);var deadline=start.addingTimeInterval(60)
        for seconds in [15,30,45]{deadline=ExercisePolling.nextDeadline(previous:deadline,now:start.addingTimeInterval(Double(seconds)),due:false,manualControlPending:false)}
        XCTAssertEqual(deadline,start.addingTimeInterval(60))
        XCTAssertEqual(ExercisePolling.nextDeadline(previous:deadline,now:deadline,due:true,manualControlPending:false),start.addingTimeInterval(120))
        let manual=ExercisePolling.nextDeadline(previous:start,now:start,due:true,manualControlPending:true)
        XCTAssertEqual(manual,start.addingTimeInterval(5))
        XCTAssertEqual(ExercisePolling.nextDeadline(previous:manual,now:start.addingTimeInterval(2),due:false,manualControlPending:true),manual)
    }
    func testExerciseLatestPreservesRawMetadataWithoutDeclaringClinicalValidity()throws{
        let value=try OuraWire.exerciseLatest(Data(hex:"2f0f25030001020f000407080978020000")!)
        XCTAssertEqual(value.result,0);XCTAssertEqual(value.flags,1);XCTAssertEqual(value.state,2)
        XCTAssertEqual(value.ageRaw,15);XCTAssertEqual(value.qualityRaw,4);XCTAssertEqual(value.lastHrRaw,120)
        let unavailable=try OuraWire.exerciseLatest(Data(hex:"2f03250320")!)
        XCTAssertEqual(unavailable.result,32);XCTAssertNil(unavailable.lastHrRaw);XCTAssertNil(unavailable.qualityRaw)
        XCTAssertThrowsError(try OuraWire.exerciseLatest(Data(hex:"2f03250300")!))
        XCTAssertThrowsError(try OuraWire.exerciseLatest(Data(hex:"2f03250420")!))
    }
    func testRecordedWorkoutCaptureSeparatesBaselineOpenSessionAndStopWithoutDuplicates(){
        var capture=RecordedWorkoutCapture();let start=Date(timeIntervalSince1970:1000)
        let trace=Data(hex:"73090b0000000001112233")!,session=Data(hex:"6c080a00000003000000")!
        capture.note(trace,at:start);capture.phase="recording";capture.requestedAt=start
        capture.note(trace,at:start.addingTimeInterval(1))
        XCTAssertEqual(capture.recordingTraceCount,0,"A duplicate from baseline must not look like fresh workout data")
        capture.note(session,at:start)
        let fresh=Data(hex:"73090c0000000001112233")!
        capture.note(fresh,at:start.addingTimeInterval(2));capture.note(fresh,at:start.addingTimeInterval(3))
        XCTAssertEqual(capture.recordingTraceCount,1);XCTAssertEqual(capture.recordsAfterSessionStart,1)
        XCTAssertEqual(capture.sessionStartRingTime,10);XCTAssertEqual(capture.firstTraceRingTime,12)
        XCTAssertEqual(capture.traceTypes["recording_1"],1)
        XCTAssertEqual(capture.diagnostic["ring_time_tick_ms"].number,100)
        XCTAssertEqual(capture.diagnostic["first_trace_ring_time_raw"].number,12)
        XCTAssertNil(capture.diagnostic["first_trace_ring_time_seconds"].number)
        capture.phase="after_stop";capture.note(Data(hex:"74060d0000001000")!,at:start.addingTimeInterval(4))
        XCTAssertEqual(capture.recordingAcmCount,0);XCTAssertEqual(capture.counts["after_stop_74"],1)
        XCTAssertNil(capture.diagnostic["raw_hex"].string)
        XCTAssertNil(capture.diagnostic["heart_rate"].number)
    }
    func testRecordedWorkoutCaptureRecognizesSessionFlushedOnlyAfterStop(){
        var capture=RecordedWorkoutCapture();let now=Date(timeIntervalSince1970:1000)
        capture.phase="recording";capture.requestedAt=now
        capture.phase="after_stop";capture.stopRequestedAt=now.addingTimeInterval(180)
        capture.note(Data(hex:"6c080a00000003000000")!,at:now.addingTimeInterval(181))
        capture.note(Data(hex:"73090c0000000001112233")!,at:now.addingTimeInterval(182))
        capture.note(Data(hex:"6c08c800000003020000")!,at:now.addingTimeInterval(183))
        XCTAssertEqual(capture.recordingTraceCount,0)
        XCTAssertEqual(capture.sessionStartRingTime,10);XCTAssertEqual(capture.lastSessionStatus,2)
        XCTAssertEqual(capture.recordsAfterSessionStart,1);XCTAssertNil(capture.firstTraceAt)
        XCTAssertEqual(capture.firstTraceAfterStopRingTime,12)
        XCTAssertEqual(capture.firstTraceAfterStopAt,now.addingTimeInterval(182))
    }
    func testExerciseCapabilityUsesUnifiedWireActivityAndPreservesLegacyEncoding()throws{
        for activity:UInt16 in [5,8,12,65535]{
            XCTAssertEqual(try OuraWire.sportParameters(activity:activity,age:40,resting:60,capabilityVersion:3).hex,"2f07290300003c32be")
        }
        XCTAssertEqual(try OuraWire.sportParameters(activity:5,age:40,resting:60,capabilityVersion:0).hex,"2f07290305003c32be")
    }
    func testPhaseBoundariesCompletionAndNoPhantomRecovery()throws{
        var c=WorkoutConfig();c.work=10;c.rest=5;c.rounds=2
        XCTAssertEqual(WorkoutEngine.position(elapsed:10,config:c).name,"Recupero")
        XCTAssertEqual(WorkoutEngine.position(elapsed:15,config:c).round,2)
        XCTAssertTrue(WorkoutEngine.position(elapsed:30,config:c).complete)
        c.rest=0;XCTAssertEqual(WorkoutEngine.position(elapsed:10,config:c).name,"Lavoro")
        c.plan=[WorkoutPhase(name:"A",duration:10,target_min:100,target_max:120,met:4)]
        XCTAssertNil(WorkoutEngine.position(elapsed:c.total,config:c).targetMin)
        c.plan[0].target_max=90;XCTAssertThrowsError(try c.validate())
    }
    func testEnergyIntegratesAcrossPhasesAndMissingMETWithoutUsingHR(){
        var c=WorkoutConfig();c.plan=[WorkoutPhase(name:"A",duration:60,met:4),WorkoutPhase(name:"B",duration:60)];c.rounds=2
        var p=BodyProfile();p.weight_kg=80
        let result=WorkoutEngine.energy(from:30,to:150,config:c,profile:p)
        XCTAssertEqual(result.gross,5.6,accuracy:0.0001);XCTAssertEqual(result.net,4.2,accuracy:0.0001)
        XCTAssertEqual(result.estimatedSeconds,60);XCTAssertEqual(result.unestimatedSeconds,60)
        p.weight_kg=nil;XCTAssertEqual(WorkoutEngine.energy(to:120,config:c,profile:p).unestimatedSeconds,120)
    }
    func testCalendarUsesRomeAcrossSpringDST(){
        let f=ISO8601DateFormatter();let now=f.date(from:"2026-03-27T00:00:00Z")!
        let rule=WeeklyRule(weekday:1,start:"13:30",end:"14:15")
        let a=WorkoutEngine.occurrences(rules:[rule],after:now,days:7)
        XCTAssertEqual(a.count,1);XCTAssertEqual(f.string(from:a[0].1),"2026-03-30T11:30:00Z")
        XCTAssertEqual(a[0].2.timeIntervalSince(a[0].1),2700)
    }
    func testQueueSurvivesReopenPartialAcknowledgementAndUnknownACK()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let url=folder.appendingPathComponent("test.sqlite")
        var first:EventStore?=try EventStore(url:url)
        let e1=try first!.append(kind:"sensor_packet",payload:.object(["raw_hex":.string("00")]))
        _=try first!.append(kind:"workout",payload:.object(["action":.string("started")]))
        let batch=try XCTUnwrap(first!.prepareBatch());first=nil
        let reopened=try EventStore(url:url)
        XCTAssertEqual(try reopened.prepareBatch()?.batch_id,batch.batch_id)
        XCTAssertThrowsError(try reopened.acknowledge(batchID:batch.batch_id,accepted:[UUID().uuidString]))
        XCTAssertEqual(reopened.pendingCount,2)
        try reopened.acknowledge(batchID:batch.batch_id,accepted:[e1.event_id])
        XCTAssertEqual(reopened.pendingCount,1);XCTAssertEqual(reopened.journalCount,2)
        let retry=try XCTUnwrap(reopened.prepareBatch());XCTAssertEqual(retry.events.count,1)
        XCTAssertEqual(retry.events[0].event_id,batch.events[1].event_id)
        XCTAssertEqual(retry.events[0].sequence,batch.events[1].sequence)
    }
    func testHistoryCheckpointAndDedupCommitTogether()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("history.sqlite"))
        let record=Data(hex:"50050200000005")!
        try store.archiveHistory(frames:[record],nextCursor:3,key:"cursor",api:[2,0,0],ring:"test-ring")
        XCTAssertEqual(store.load(UInt32.self,key:"cursor"),3);XCTAssertEqual(store.pendingCount,1)
        try store.archiveHistory(frames:[record],nextCursor:3,key:"cursor",api:[2,0,0],ring:"test-ring")
        XCTAssertEqual(store.pendingCount,1);XCTAssertEqual(store.metrics().steps,5)
    }
    func testGeofenceDetectionSustainedFreshSamplesNoDuplicateAndManualPriority(){
        let now=Date(timeIntervalSince1970:1_000)
        var engine=GymDetectionEngine()
        let baseline=(0..<6).map{TimedBPM(at:now.addingTimeInterval(Double($0)-10),bpm:60)}
        engine.enter(at:now,recent:baseline)
        XCTAssertEqual(engine.state.baseline,60)
        XCTAssertNil(engine.ingest(TimedBPM(at:now.addingTimeInterval(-60),bpm:100),now:now,manualWorkout:false))
        for i in stride(from:0,to:30,by:5){XCTAssertNil(engine.ingest(TimedBPM(at:now.addingTimeInterval(Double(i)),bpm:80),now:now.addingTimeInterval(Double(i)),manualWorkout:false))}
        let id=engine.ingest(TimedBPM(at:now.addingTimeInterval(30),bpm:80),now:now.addingTimeInterval(30),manualWorkout:false)
        XCTAssertNotNil(id)
        XCTAssertNil(engine.ingest(TimedBPM(at:now.addingTimeInterval(35),bpm:80),now:now.addingTimeInterval(35),manualWorkout:false))
        engine.resolve();XCTAssertNil(engine.ingest(TimedBPM(at:now.addingTimeInterval(40),bpm:100),now:now.addingTimeInterval(40),manualWorkout:true))
    }
    func testGeofenceMissingBaselineCollectsInsteadOfTriggering(){
        let now=Date(timeIntervalSince1970:1_000);var engine=GymDetectionEngine();engine.enter(at:now,recent:[])
        for i in 0...6{let t=now.addingTimeInterval(Double(i*5));XCTAssertNil(engine.ingest(TimedBPM(at:t,bpm:90),now:t,manualWorkout:false))}
        XCTAssertEqual(engine.state.baseline,90);XCTAssertNil(engine.state.candidateSessionID)
    }
    func testRawFramingPreservesFirstFrameBeforeLaterUnsupportedTransport()throws{
        var framer=OuraFramer();var recorded:[Data]=[]
        let sensor=Data(hex:"410401000000")!,nonce=Data(hex:"2f102c000102030405060708090a0b0c0d0e")!
        XCTAssertThrowsError(try framer.consume(sensor+nonce+Data([0xfe,0])){frame in if !OuraWire.sensitive(frame){recorded.append(frame)}})
        XCTAssertEqual(recorded,[sensor]);XCTAssertEqual(framer.opaqueRejected,Data([0xfe,0]))
    }
    func testGeofenceGapRequiresNewBaselineAndRestoredCandidateCannotDuplicate()throws{
        let now=Date(timeIntervalSince1970:1000);var engine=GymDetectionEngine()
        engine.enter(at:now,recent:(0..<6).map{TimedBPM(at:now.addingTimeInterval(Double($0)-10),bpm:60)})
        _=engine.ingest(TimedBPM(at:now,bpm:80),now:now,manualWorkout:false)
        let later=now.addingTimeInterval(200)
        XCTAssertNil(engine.ingest(TimedBPM(at:later,bpm:90),now:later,manualWorkout:false));XCTAssertNil(engine.state.baseline)
        engine.state.candidateSessionID=UUID();engine.state.candidateAt=later
        let restored=try JSONDecoder().decode(DetectionState.self,from:JSONEncoder().encode(engine.state));engine.state=restored
        XCTAssertNil(engine.ingest(TimedBPM(at:later.addingTimeInterval(1),bpm:150),now:later.addingTimeInterval(1),manualWorkout:false))
    }

    func testElapsedContinuesAfterPlanWhileUnconfiguredTailHasNoEnergyEstimate(){
        var config=WorkoutConfig();config.rounds=1;config.plan=[WorkoutPhase(name:"A",duration:60,met:4)]
        var profile=BodyProfile();profile.weight_kg=80
        let start=Date(timeIntervalSince1970:1000)
        let session=WorkoutSession(id:UUID(),activityName:"Test",startedAt:start,config:config,profile:profile,runningSince:start)
        XCTAssertEqual(session.elapsed(at:start.addingTimeInterval(90)),90)
        let energy=WorkoutEngine.energy(to:90,config:config,profile:profile)
        XCTAssertEqual(energy.gross,5.6,accuracy:0.001);XCTAssertEqual(energy.unestimatedSeconds,30)
    }
    func testDisplayBoundsNeverDiscardTheProtocolValueButExcludeDetection(){
        let frame=Data(hex:"2f0f280201020000011000000000000000")!
        XCTAssertEqual(try OuraWire.live(frame,api:[2,0,0]).bpm,60000)
        XCTAssertFalse(OuraWire.displaySupported(60000));XCTAssertFalse(OuraWire.displaySupported(19));XCTAssertTrue(OuraWire.displaySupported(250))
        var engine=GymDetectionEngine();let now=Date();engine.enter(at:now,recent:[])
        XCTAssertNil(engine.ingest(TimedBPM(at:now,bpm:300),now:now,manualWorkout:false));XCTAssertTrue(engine.state.baselineSamples.isEmpty)
    }
    func serverSession(_ state:String="paused")throws->JSONValue{
        .object(["id":.string("00112233445566778899aabbccddeeff"),"status":.string(state),"started_at":.number(1000),"active_seconds":.number(300),"activity_name":.string("Sessione web"),"config":try JSONValue.encoded(WorkoutConfig()),"profile":try JSONValue.encoded(BodyProfile())])
    }
    func testServerAdoptionRetainsIdentityAndPausedTimeWithoutStartingAgain()throws{
        let paused=try XCTUnwrap(SessionReconciliation.decode(serverSession(),receivedAt:Date(timeIntervalSince1970:1500)))
        XCTAssertEqual(paused.remoteID,"00112233445566778899aabbccddeeff");XCTAssertEqual(paused.id.uuidString,"00112233-4455-6677-8899-AABBCCDDEEFF")
        XCTAssertEqual(paused.state,"paused");XCTAssertEqual(paused.elapsed(at:Date(timeIntervalSince1970:1600)),300)
        let active=try XCTUnwrap(SessionReconciliation.decode(serverSession("active"),receivedAt:Date(timeIntervalSince1970:1500)))
        XCTAssertEqual(active.elapsed(at:Date(timeIntervalSince1970:1510)),310)
        XCTAssertEqual(SessionReconciliation.decode(try serverSession("completed"),receivedAt:Date())?.state,"ended")
        XCTAssertEqual(SessionReconciliation.decode(try serverSession("rejected"),receivedAt:Date())?.state,"false_detection")
    }
    func testServerReconciliationCannotOverwritePendingOfflineWorkout()throws{
        let folder=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString);defer{try? FileManager.default.removeItem(at:folder)}
        let store=try EventStore(url:folder.appendingPathComponent("sessions.sqlite"))
        let config=WorkoutConfig();let local=WorkoutSession(id:UUID(),activityName:"Offline",startedAt:Date(),config:config,profile:BodyProfile(),runningSince:Date())
        try store.workout(local,payload:.object(["action":.string("started")]))
        let remote=try XCTUnwrap(SessionReconciliation.decode(serverSession(),receivedAt:Date()))
        try store.reconcileSessions([remote],active:remote)
        XCTAssertEqual(store.load(WorkoutSession.self,key:"activeWorkout")?.id,local.id)
        let batch=try XCTUnwrap(store.prepareBatch());try store.acknowledge(batchID:batch.batch_id,accepted:batch.events.map{$0.event_id})
        XCTAssertEqual(store.pendingWorkoutCount,0)
        try store.reconcileSessions([remote],active:remote)
        XCTAssertEqual(store.load(WorkoutSession.self,key:"activeWorkout")?.id,remote.id)
        XCTAssertEqual(store.journalCount,1);XCTAssertEqual(store.pendingCount,0)
        try store.reconcileSessions([],active:nil);XCTAssertNil(store.load(WorkoutSession.self,key:"activeWorkout"))
    }

}

final class OuraBatteryProtocolTests:XCTestCase {
    func testBatteryPercentageAndChargingFieldsRemainDistinct()throws{
        let low=try OuraWire.battery(Data(hex:"0d030a0001")!)
        XCTAssertEqual(low.level,10);XCTAssertEqual(low.isCharging,false)
        XCTAssertEqual(low.chargingProgressRaw,0);XCTAssertEqual(low.chargingRecommendationRaw,1)
        let full=try OuraWire.battery(Data(hex:"0d03646400")!)
        XCTAssertEqual(full.level,100);XCTAssertEqual(full.isCharging,true)
        XCTAssertEqual(full.chargingProgressRaw,100)
    }
    func testBatteryMissingWrongLengthAndInvalidPercentageAreRejected(){
        for hex in ["0d020a00","0d040a0001","0c030a0001","0d037f0000","0d03ff0000"]{
            XCTAssertThrowsError(try OuraWire.battery(Data(hex:hex)!))
        }
    }
    func testChargeStateUsesRingProgressAndLegacySnapshotStaysUnknown()throws{
        XCTAssertEqual(RingChargeState(progress:0),.idle)
        XCTAssertEqual(RingChargeState(progress:1),.charging)
        XCTAssertEqual(RingChargeState(progress:99),.charging)
        XCTAssertEqual(RingChargeState(progress:100),.full)
        XCTAssertNil(RingChargeState(progress:255));XCTAssertNil(RingChargeState(progress:nil))
        let data=Data("{\"level\":51,\"at\":0}".utf8)
        let old=try JSONDecoder().decode(RingBatterySnapshot.self,from:data)
        XCTAssertEqual(old.level,51);XCTAssertNil(old.chargingProgressRaw)
    }
    func testBatteryExtendedGen3PayloadRetainsPercentage(){
        XCTAssertEqual(try OuraWire.battery(Data(hex:"0d06330000000000")!).level,51)
        XCTAssertEqual(try OuraWire.battery(Data(hex:"0d040a000100")!).level,10)
    }
    func testBatteryUnknownChargeProgressNeverClaimsCharging()throws{
        let value=try OuraWire.battery(Data(hex:"0d0332ff02")!)
        XCTAssertEqual(value.level,50);XCTAssertNil(value.isCharging)
        XCTAssertEqual(value.chargingProgressRaw,255);XCTAssertEqual(value.chargingRecommendationRaw,2)
    }
}


private final class SnapshotWrites:@unchecked Sendable {
    private let lock=NSLock();private var snapshots:[JSONValue]=[]
    func append(_ value:JSONValue){lock.lock();snapshots.append(value);lock.unlock()}
    var values:[JSONValue]{lock.lock();defer{lock.unlock()};return snapshots}
}
private final class HomePulseHTTPFixture:URLProtocol {
    static var reply:((URLRequest)throws->Data)?
    override class func canInit(with request:URLRequest)->Bool {request.url?.host == "synthetic.example"}
    override class func canonicalRequest(for request:URLRequest)->URLRequest {request}
    override func startLoading(){do{
        guard let reply=Self.reply,let url=request.url else{throw URLError(.badServerResponse)}
        let data=try reply(request)
        client?.urlProtocol(self,didReceive:HTTPURLResponse(url:url,statusCode:200,httpVersion:"HTTP/1.1",headerFields:["Content-Type":"application/json"])!,cacheStoragePolicy:.notAllowed)
        client?.urlProtocol(self,didLoad:data);client?.urlProtocolDidFinishLoading(self)
    }catch{client?.urlProtocol(self,didFailWithError:error)}}
    override func stopLoading(){}
}
