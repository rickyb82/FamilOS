import pytest
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
        from app.policy.permissions import tool_allowed
        assert tool_allowed({'allowed_tools':['mcp'],'apps':['wellness']},'allenamento_analisi')
        assert not tool_allowed({'allowed_tools':['mcp'],'apps':[]},'allenamento_analisi')
        share=Share(family_id=user.family_id,resource_type='conversation',resource_id=conv.id,owner_id=user.id,target_user_id=None)
        db.add(share);await db.commit()
        assert (await tools.state({},ctx)).is_error
        before=archive.settings(user.id)
        assert (await tools.plan({'config':{'rounds':3}},ctx)).is_error
        assert archive.settings(user.id)==before
        await db.delete(share);await db.delete(conv);await db.commit()
