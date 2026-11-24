import XCTest
@testable import HomePulse

final class HomePulseTests:XCTestCase {
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
