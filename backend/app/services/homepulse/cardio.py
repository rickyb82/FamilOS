"""Time in explicit phase targets. Gaps and pauses never become training time."""
from bisect import bisect_left
import math


def phase_windows(start,end,config,events):
    plan=config.get('plan') or [{'name':'Lavoro','duration':config.get('work',40)},{'name':'Recupero','duration':config.get('rest',20)}]
    cycle=sum(p['duration'] for p in plan)
    segments=[];running=True;cursor=start;elapsed=0.0
    for event in sorted(events,key=lambda e:(e['captured_at'],e.get('sequence',0))):
        at=min(end,max(start,event['captured_at']));action=event['payload'].get('action')
        if event['captured_at']>end:continue
        if action in ('paused','ended','false_detection') and running:
            if at>cursor:segments.append((cursor,at,elapsed));elapsed+=at-cursor
            running=False
        elif action=='resumed' and not running:cursor=at;running=True
    if running and end>cursor:segments.append((cursor,end,elapsed));elapsed+=end-cursor
    windows=[]
    for a,b,offset in segments:
        for round_no in range(config.get('rounds',8)):
            phase_at=cycle*round_no
            for index,phase in enumerate(plan):
                stop=phase_at+phase['duration'];left=max(phase_at,offset);right=min(stop,offset+b-a)
                if right>left:windows.append((a+left-offset,a+right-offset,index,phase,a,b))
                phase_at=stop
    return sorted(windows,key=lambda w:w[0]),elapsed


def analyze(start,end,config,events,points):
    windows,elapsed=phase_windows(start,end,config,events)
    grouped={}
    for point in points:
        t,v=point['t'],point['v']
        if type(t) in (int,float) and type(v) in (int,float) and math.isfinite(t) and math.isfinite(v) and start<=t<=end and 20<=v<=250:
            grouped.setdefault(t,set()).add(v)
    # Conflicting simultaneous values do not establish an interval.
    values=sorted((t,next(iter(v))) for t,v in grouped.items() if len(v)==1)
    stamps=[p[0] for p in values];bad_times=sorted(t for t,vs in grouped.items() if len(vs)>1);phases={};target_seconds=0.0
    for a,b,index,phase,active_start,active_end in windows:
        low,high=phase.get('target_min'),phase.get('target_max')
        if low is None or high is None:continue
        target_seconds+=b-a
        row=phases.setdefault(index,{'phase_index':index,'name':phase['name'],'target_min':low,'target_max':high,'target_seconds':0.0,'below_seconds':0.0,'inside_seconds':0.0,'above_seconds':0.0,'samples':0})
        row['target_seconds']+=b-a
        left=max(0,bisect_left(stamps,a)-1)
        for j in range(left,len(values)):
            t,v=values[j]
            if t>=b:break
            if t>=a:row['samples']+=1
            if j+1>=len(values):break
            next_t=values[j+1][0]
            bad=bisect_left(bad_times,t)
            if t<active_start or next_t>active_end or next_t-t>8 or bad<len(bad_times) and bad_times[bad]<=next_t:continue
            seconds=max(0,min(b,next_t)-max(a,t))
            key='below_seconds' if v<low else 'above_seconds' if v>high else 'inside_seconds'
            row[key]+=seconds
    rows=list(phases.values())
    for row in rows:row['unknown_seconds']=max(0,row['target_seconds']-sum(row[k] for k in ('below_seconds','inside_seconds','above_seconds')))
    totals={key:sum(row[key] for row in rows) for key in ('below_seconds','inside_seconds','above_seconds','unknown_seconds','samples')}
    return {**totals,'target_seconds':target_seconds,'elapsed_active_seconds':elapsed,'unconfigured_seconds':max(0,elapsed-target_seconds),
            'coverage_fraction':(target_seconds-totals['unknown_seconds'])/target_seconds if target_seconds else None,
            'phases':rows,'max_gap_seconds':8,'method':'intervalli tra campioni reali consecutivi; valore iniziale; pause e vuoti esclusi'}
