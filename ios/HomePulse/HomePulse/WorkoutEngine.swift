import Foundation

enum WorkoutEngine {
    static func position(elapsed:Double, config:WorkoutConfig)->PhasePosition {
        let phases=config.phases
        guard let last=phases.last else {return PhasePosition(name:"Completato",round:config.rounds,remaining:0,index:0,complete:true)}
        if elapsed>=config.total {return PhasePosition(name:"Completato",round:config.rounds,remaining:0,index:phases.count-1,complete:true)}
        let cycle=Double(phases.reduce(0){$0+$1.duration})
        guard cycle>0 else {return PhasePosition(name:last.name,round:1,remaining:0,index:0,complete:true)}
        let roundIndex=Int(max(0,elapsed)/cycle)
        var at=max(0,elapsed)-Double(roundIndex)*cycle
        for (i,p) in phases.enumerated() {
            if at<Double(p.duration) {return PhasePosition(name:p.name,round:roundIndex+1,remaining:Int(ceil(Double(p.duration)-at)),index:i,targetMin:p.target_min,targetMax:p.target_max,met:p.met,complete:false)}
            at-=Double(p.duration)
        }
        return PhasePosition(name:"Completato",round:config.rounds,remaining:0,index:phases.count-1,complete:true)
    }
    static func energy(from start:Double=0,to end:Double, config:WorkoutConfig,profile:BodyProfile)->Energy {
        let realStart=max(0,start),realEnd=max(realStart,end)
        let a=min(config.total,realStart),b=min(config.total,realEnd),extra=max(0,realEnd-max(realStart,config.total))
        guard let weight=profile.weight_kg else {return Energy(unestimatedSeconds:realEnd-realStart)}
        let phases=config.phases, cycle=Double(phases.reduce(0){$0+$1.duration})
        guard cycle>0 else {return Energy(unestimatedSeconds:b-a)}
        func integral(_ time:Double)->Energy {
            let n=min(config.rounds,Int(time/cycle)); var remainder=time-Double(n)*cycle
            var result=Energy()
            for phase in phases {
                let duration=Double(n*phase.duration)+min(max(0,remainder),Double(phase.duration))
                remainder=max(0,remainder-Double(phase.duration))
                if let met=phase.met {
                    result.gross+=duration*met*3.5*weight/200/60
                    result.net+=duration*(met-1)*3.5*weight/200/60
                    result.estimatedSeconds+=duration
                } else {result.unestimatedSeconds+=duration}
            }
            return result
        }
        let before=integral(a),after=integral(b)
        return Energy(gross:max(0,after.gross-before.gross),net:max(0,after.net-before.net),estimatedSeconds:max(0,after.estimatedSeconds-before.estimatedSeconds),unestimatedSeconds:max(0,after.unestimatedSeconds-before.unestimatedSeconds)+extra)
    }
    static func occurrences(rules:[WeeklyRule],after now:Date,days:Int=21)->[(WeeklyRule,Date,Date)] {
        var result:[(WeeklyRule,Date,Date)]=[]
        for rule in rules {
            guard let zone=TimeZone(identifier:rule.timezone), (1...7).contains(rule.weekday) else {continue}
            let s=rule.start.split(separator:":").compactMap{Int($0)},e=rule.end.split(separator:":").compactMap{Int($0)}
            guard s.count==2,e.count==2,(0...23).contains(s[0]),(0...59).contains(s[1]),(0...23).contains(e[0]),(0...59).contains(e[1]) else {continue}
            var calendar=Calendar(identifier:.iso8601);calendar.timeZone=zone
            for d in 0..<days {
                guard let day=calendar.date(byAdding:.day,value:d,to:calendar.startOfDay(for:now)),
                      (calendar.component(.weekday,from:day)+5)%7+1==rule.weekday,
                      let start=calendar.date(bySettingHour:s[0],minute:s[1],second:0,of:day),
                      var end=calendar.date(bySettingHour:e[0],minute:e[1],second:0,of:day) else {continue}
                if end<=start {end=calendar.date(byAdding:.day,value:1,to:end) ?? end}
                if end>=now {result.append((rule,start,end))}
            }
        }
        return result.sorted{$0.1<$1.1}
    }
}
