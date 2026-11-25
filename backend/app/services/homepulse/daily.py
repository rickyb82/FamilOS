"""Daily estimated energy from acquired minute METs; no invented HR calories.

Resting: Mifflin/St Jeor 1990 https://pubmed.ncbi.nlm.nih.gov/2305711/
MET convention: https://pacompendium.com/corrected-mets/
Active energy subtracts one conventional MET, consistently with workout.py.
Resting and active estimates are combined once. Food thermogenesis and activity
without usable acquired METs are not manufactured from missing data.
"""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import math
from .workout import composition


def window(now,date=None,zone='Europe/Rome'):
    tz=ZoneInfo(zone);day=datetime.fromtimestamp(now,tz).date() if date is None else datetime.strptime(date,'%Y-%m-%d').date()
    start=datetime.combine(day,datetime.min.time(),tz).timestamp()
    stop=datetime.combine(day+timedelta(days=1),datetime.min.time(),tz).timestamp()
    return str(day),start,max(start,min(now,stop))


def estimate(profile,intervals,start,end):
    weight=profile.get('weight_kg');height=profile.get('height_cm');age=profile.get('age');sex=profile.get('energy_sex')
    body=composition(profile);warnings=[]
    if body['basal_kcal_day'] is not None:
        basals=[body['basal_kcal_day']];rest_method='Cunningham-1980'
    elif all(type(v) in (int,float) and math.isfinite(v) for v in (weight,height,age)):
        base=10*weight+6.25*height-5*age
        basals=[base+(5 if sex=='male' else -161)] if sex in ('male','female') else [base-161,base+5]
        rest_method='Mifflin-St-Jeor-1990'
        if len(basals)>1:warnings.append('Parametro fisiologico non specificato: intervallo fra le due formule, non un intervallo di confidenza.')
        if 'Da chiarire' in body['composition_status']:warnings.append('Composizione non complementare: non usata per ricavare massa magra o basale.')
    else:basals=[];rest_method=None;warnings.append('Completa peso, altezza ed età per la stima di base.')
    elapsed=max(0,end-start);base_low=min(basals)*elapsed/86400 if basals else None;base_high=max(basals)*elapsed/86400 if basals else None
    events={start:[],end:[]};valid=[]
    for item in intervals:
        a,b=max(start,item['start']),min(end,item['end']);met=item['met']
        if b<=a or type(met) not in (int,float) or not math.isfinite(met) or not 0<met<=38.2:continue
        index=len(valid);valid.append(item);events.setdefault(a,[]).append((1,index));events.setdefault(b,[]).append((-1,index))
    # Hour boundaries also make the returned series exact on 23/25-hour days.
    for i in range(1,int(elapsed//3600)+1):events.setdefault(start+i*3600,[])
    buckets=[{'start':start+i*3600,'end':min(end,start+(i+1)*3600),'active_kcal':0.0,'estimated_seconds':0.0} for i in range(max(1,math.ceil(elapsed/3600)))]
    active={};covered=0.0;energy=0.0;sources=set()
    stamps=sorted(events)
    for i,t in enumerate(stamps[:-1]):
        for direction,index in events[t]:
            if direction==1:active[index]=valid[index]
            else:active.pop(index,None)
        stop=stamps[i+1]
        if not active or weight is None:continue
        priority=max(v.get('priority',0) for v in active.values());chosen=[v for v in active.values() if v.get('priority',0)==priority]
        mets={round(v['met'],6) for v in chosen}
        if len(mets)!=1:continue # Conflicting overlaps do not become extra kcal.
        met=next(iter(mets));seconds=stop-t;kcal=max(0,met-1)*3.5*weight/200/60*seconds
        energy+=kcal;covered+=seconds;sources.update(v['source'] for v in chosen)
        bucket=buckets[min(len(buckets)-1,int((t-start)//3600))];bucket['active_kcal']+=kcal;bucket['estimated_seconds']+=seconds
    for b in buckets:
        span=max(0,b['end']-b['start']);b['base_kcal']=min(basals)*span/86400 if len(set(basals))==1 else None
        b['total_kcal']=b['base_kcal']+b['active_kcal'] if b['base_kcal'] is not None else None
        b['unestimated_activity_seconds']=max(0,span-b['estimated_seconds'])
    base=base_low if base_low==base_high else None
    return {'enabled':True,'window_start':start,'window_end':end,'base_kcal':base,'base_kcal_range':{'min':base_low,'max':base_high},
        'active_kcal':energy if weight is not None else None,'total_kcal':base+energy if base is not None and weight is not None else None,
        'total_kcal_range':{'min':base_low+energy if base_low is not None and weight is not None else None,'max':base_high+energy if base_high is not None and weight is not None else None},
        'basal_kcal_day':basals[0] if len(set(basals))==1 else None,'rest_method':rest_method,'activity_method':'NET-MET-3.5-weight/200','profile_basis':'current_profile',
        'estimated_activity_seconds':covered,'unestimated_activity_seconds':max(0,elapsed-covered),'coverage_fraction':covered/elapsed if elapsed else None,
        'activity_sources':sorted(sources),'hourly':buckets,'estimated':True,'complete_daily_expenditure':False,
        'warnings':warnings+['Stima da profilo e MET: non calorimetria. I MET dell’anello sono stime del firmware con qualità individuale non verificata.','Attività senza MET e termogenesi alimentare non incluse; i vuoti non sono sedentarietà.'],
        'sources':['https://pubmed.ncbi.nlm.nih.gov/2305711/','https://pacompendium.com/corrected-mets/']}
