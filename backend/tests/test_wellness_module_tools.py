import pytest
import json
from unittest.mock import Mock
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User,Conversation,Share
from app.config import get_settings
from app.services.homepulse import module
from app.services.homepulse.store import HomePulseStore
from app.tools import wellness_tools as tools
from app.tools.registry import ToolContext
from app.api import wellness

@pytest.mark.asyncio
async def test_optional_install_admin_only_disable_preserves_archive(admin,anna,kid,tmp_path,monkeypatch):
    monkeypatch.setattr(get_settings(),'data_dir',tmp_path)
    assert module.enabled() is False
    assert (await admin.get('/api/admin/wellness-module')).json()['enabled'] is False
    for client in (anna,kid):assert (await client.put('/api/admin/wellness-module',json={'enabled':True,'confirm_maintenance':True})).status_code==403
    assert (await admin.put('/api/admin/wellness-module',json={'enabled':True,'confirm_maintenance':True})).status_code==200
    archive=HomePulseStore(tmp_path/'wellness');archive.update_settings('synthetic',{'profile':{'weight_kg':80}})
    assert (await admin.put('/api/admin/wellness-module',json={'enabled':False,'confirm_maintenance':True})).status_code==200
    assert module.enabled() is False and archive.settings('synthetic')['profile']['weight_kg']==80
    assert (await anna.get('/api/wellness/v1/state')).status_code==404
    never=Mock(side_effect=AssertionError('disabled module must not process archives'))
    monkeypatch.setattr(wellness,'store',never);await wellness.process_history_once();never.assert_not_called()

@pytest.mark.asyncio
async def test_tools_are_owner_scoped_and_reject_shared_conversation(anna,tmp_path,monkeypatch):
    monkeypatch.setattr(module,'enabled',lambda:True)
    archive=HomePulseStore(tmp_path/'private');monkeypatch.setattr(wellness,'store',lambda:archive)
    async with SessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
        conv=Conversation(owner_id=user.id,family_id=user.family_id,title='Synthetic workout test');db.add(conv);await db.commit()
        ctx=ToolContext(user=user,policy={},db=db,conversation_id=conv.id)
        response=await tools.plan({'config':{'rounds':2,'plan':[{'name':'Test','duration':60,'target_min':100,'target_max':120}]}},ctx)
        assert not response.is_error
        assert archive.settings(user.id)['config']['rounds']==2
        assert archive.settings('someone-else')['config']['rounds']!=2
        assert (await tools.analysis({'session_id':'other-owner-session'},ctx)).is_error
        assert (await tools.samples({'session_id':'other-owner-session'},ctx)).is_error
        from app.policy.permissions import tool_allowed
        assert tool_allowed({'allowed_tools':['mcp'],'apps':['wellness']},'allenamento_analisi')
        assert not tool_allowed({'allowed_tools':['mcp'],'apps':[]},'allenamento_analisi')
        assert tool_allowed({'allowed_tools':['mcp'],'apps':['wellness']},'allenamento_campioni')
        assert not tool_allowed({'allowed_tools':['mcp'],'apps':[]},'allenamento_campioni')
        for name in ('allenamento_grafico','allenamento_energia'):
            assert tool_allowed({'allowed_tools':['mcp'],'apps':['wellness']},name)
            assert not tool_allowed({'allowed_tools':['mcp'],'apps':[]},name)
        active=archive.manual_session(user.id,'start')['active_session']
        with archive.connection(True) as point_db:
            for i in range(1000):
                point_db.execute('INSERT INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',(user.id,'heart_rate',active['started_at'],100,'synthetic',.9,json.dumps({'warnings':['synthetic quality metadata']*4,'interpolated':True,'provisional':True,'model':'WHR-U2.5.1'}),str(i)))
        sample=await tools.samples({'session_id':active['id'],'limit':1000},ctx)
        assert not sample.is_error and len(sample.content)<40_000
        page=json.loads(sample.content)
        assert page['has_more'] and page['returned']<1000 and page['next_cursor']
        next_page=await tools.samples({'session_id':active['id'],'limit':1000,'cursor':page['next_cursor']},ctx)
        assert not next_page.is_error and json.loads(next_page.content)['returned']>0
        graph=await tools.chart({'session_id':active['id']},ctx)
        assert not graph.is_error and graph.artifacts[0]['kind']=='html'
        assert graph.artifacts[0]['conversation_id']==conv.id
        assert '<canvas' in graph.artifacts[0]['content'] or "node('canvas'" in graph.artifacts[0]['content'] and '1000' in graph.content
        assert not (await tools.energy({},ctx)).is_error
        assert (await tools.chart({'session_id':'another-owner'},ctx)).is_error
        share=Share(family_id=user.family_id,resource_type='conversation',resource_id=conv.id,owner_id=user.id,target_user_id=None)
        db.add(share);await db.commit()
        assert (await tools.state({},ctx)).is_error
        assert (await tools.samples({'session_id':active['id']},ctx)).is_error
        assert (await tools.chart({'session_id':active['id']},ctx)).is_error
        assert (await tools.energy({},ctx)).is_error
        before=archive.settings(user.id)
        assert (await tools.plan({'config':{'rounds':3}},ctx)).is_error
        assert archive.settings(user.id)==before
        await db.delete(share);await db.delete(conv);await db.commit()
