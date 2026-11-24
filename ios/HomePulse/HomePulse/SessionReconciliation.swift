import Foundation
import CryptoKit

enum SessionReconciliation {
    static func identity(_ value:String)->UUID{
        if let uuid=UUID(uuidString:value){return uuid}
        if value.count==32{let a=Array(value);let parts=[String(a[0..<8]),String(a[8..<12]),String(a[12..<16]),String(a[16..<20]),String(a[20..<32])];if let uuid=UUID(uuidString:parts.joined(separator:"-")){return uuid}}
        var bytes=Array(SHA256.hash(data:Data(value.utf8)).prefix(16));bytes[6]=(bytes[6]&15)|0x50;bytes[8]=(bytes[8]&63)|0x80
        return NSUUID(uuidBytes:bytes) as UUID
    }
    static func decode(_ value:JSONValue,receivedAt:Date)->WorkoutSession?{
        guard let remote=value["id"].string,!remote.isEmpty,let status=value["status"].string,["active","paused","completed","rejected","ended","false_detection"].contains(status),let start=value["started_at"].number,start.isFinite,let seconds=value["active_seconds"].number,seconds.isFinite,seconds>=0,
            let configData=try? JSONEncoder().encode(value["config"]),let config=try? JSONDecoder().decode(WorkoutConfig.self,from:configData),
            let profileData=try? JSONEncoder().encode(value["profile"]),let profile=try? JSONDecoder().decode(BodyProfile.self,from:profileData),
            (try? config.validate()) != nil,(try? profile.validate()) != nil else{return nil}
        var result=WorkoutSession(id:identity(remote),activityName:value["activity_name"].string ?? "Allenamento",startedAt:Date(timeIntervalSince1970:start),config:config,profile:profile,accumulated:seconds,runningSince:status=="active" ? receivedAt:nil,state:status=="active" ? "running":status=="completed" ? "ended":status=="rejected" ? "false_detection":status)
        result.remoteID=remote
        if let end=value["ended_at"].number,end.isFinite{result.endedAt=Date(timeIntervalSince1970:end)}
        if case .bool(let auto)=value["auto_detected"]{result.auto_detected=auto}
        if case .bool(let provisional)=value["provisional"]{result.provisional=provisional}
        result.source=value["source"].string ?? "server"
        return result
    }
}
