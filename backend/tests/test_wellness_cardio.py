from app.services.homepulse.cardio import analyze
CFG={'rounds':1,'plan':[{'name':'Lavoro','duration':10,'target_min':100,'target_max':120},{'name':'Recupero','duration':10,'target_min':80,'target_max':100}]}
def point(t,v):return {'t':t,'v':v}
def action(t,a):return {'captured_at':t,'payload':{'action':a}}

def test_targets_split_time_at_phase_boundary_and_keep_exact_limits_inside():
    stats=analyze(0,20,CFG,[],[point(0,100),point(5,120),point(10,90),point(15,110),point(20,95)])
    assert stats['inside_seconds']==15 and stats['above_seconds']==5
    assert stats['unknown_seconds']==0 and stats['coverage_fraction']==1
    assert stats['phases'][0]['inside_seconds']==10

def test_dropout_is_unknown_not_an_invented_zone():
    stats=analyze(0,20,CFG,[],[point(0,110),point(2,110),point(18,90),point(20,90)])
    assert stats['inside_seconds']==4 and stats['unknown_seconds']==16
    assert stats['coverage_fraction']==0.2

def test_pause_excluded_and_a_pair_spanning_pause_establishes_no_interval():
    stats=analyze(0,15,CFG,[action(5,'paused'),action(10,'resumed'),action(15,'ended')],[point(0,110),point(4,110),point(11,110),point(15,110)])
    assert stats['elapsed_active_seconds']==10 and stats['target_seconds']==10
    assert stats['inside_seconds']==8 and stats['unknown_seconds']==2

def test_single_sample_or_conflicting_timestamp_does_not_create_duration():
    assert analyze(0,10,CFG,[],[point(1,110)])['unknown_seconds']==10
    assert analyze(0,10,CFG,[],[point(0,110),point(5,110),point(5,140),point(10,110)])['unknown_seconds']==10

def test_unconfigured_or_extra_plan_time_is_not_assigned_a_target():
    stats=analyze(0,20,{'rounds':1,'plan':[{'name':'Scelta libera','duration':5}]},[],[point(0,100),point(5,100)])
    assert stats['target_seconds']==0 and stats['unconfigured_seconds']==20
    assert stats['coverage_fraction'] is None
