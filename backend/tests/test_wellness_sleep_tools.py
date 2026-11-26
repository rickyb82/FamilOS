"""Emily can read existing sleep in its owner's private chat, without uploads."""
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api import wellness
from app.db import SessionLocal
from app.models import Conversation, Share, User
from app.services.homepulse import module
from app.services.homepulse.daily import window
from app.services.homepulse.sleep import NATIVE_ABI
from app.services.homepulse.store import HomePulseStore
from app.tools import wellness_tools
from app.tools.registry import ToolContext


def native(start,end):
    return {'bedtime_start_utc_seconds':int(start),'bedtime_end_utc_seconds':int(end),'time_in_bed_seconds':end-start,'bedtime_start_time_zone_minutes':60,'native_version':[3,6,2],'native_abi':NATIVE_ABI,'phases_30_seconds':'1234'}


def run(*periods):
    return {'status':'ok','process_true':True,'deinit_returned':True,'version':[3,6,2],'mapping':{'complete':True},'sleep_native_abi':NATIVE_ABI,'sleep_capture_complete':True,'sleep_outputs':list(periods)}


@pytest.mark.asyncio
async def test_sleep_tool_reaches_chat_discovery_and_small_local_models(anna,monkeypatch):
    monkeypatch.setattr(module,'enabled',lambda:True)
    reply=await anna.get('/api/chat/providers')
    assert reply.status_code==200
    tool=next(t for t in reply.json()['tools'] if t['name']=='allenamento_sonno')
    assert tool['group']=='mcp' and 'sonno' in tool['description']
    from app.services.chat_service import LOCAL_TOOLS
    from app.policy.permissions import tool_allowed
    assert 'allenamento_sonno' in LOCAL_TOOLS
    assert tool_allowed({'allowed_tools':['mcp'],'apps':['wellness']},'allenamento_sonno')
    assert not tool_allowed({'allowed_tools':['mcp'],'apps':[]},'allenamento_sonno')
    assert not tool_allowed({'allowed_tools':[],'apps':['wellness']},'allenamento_sonno')


@pytest.mark.asyncio
async def test_sleep_tool_date_refers_to_end_day_and_pages_only_owner_periods(anna,tmp_path,monkeypatch):
    monkeypatch.setattr(module,'enabled',lambda:True)
    archive=HomePulseStore(tmp_path/'private');monkeypatch.setattr(wellness,'store',lambda:archive)
    _,today,_=window(time.time());date=datetime.fromtimestamp(today-12*3600,ZoneInfo('Europe/Rome')).date().isoformat()
    _,yesterday,_=window(time.time(),date)
    async with SessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
        conv=Conversation(owner_id=user.id,family_id=user.family_id,title='Synthetic sleep tool');db.add(conv);await db.commit()
        ctx=ToolContext(user=user,policy={},db=db,conversation_id=conv.id)
        archive.publish_processed(user.id,[],run(native(yesterday-3600,yesterday+3600),native(yesterday-1800,yesterday+7200),native(yesterday-7200,yesterday-3600)))
        archive.publish_processed('someone-else',[],run(native(yesterday-3000,yesterday+1800)))
        with archive.connection(True) as points:
            archive._point(points,user.id,'respiratory_rate',yesterday+1200,16,'oura-firmware-sleep-period-history','synthetic-breath')
        first=await wellness_tools.sleep({'date':date,'limit':1},ctx)
        assert not first.is_error
        page=json.loads(first.content)
        assert page['total_periods']==2 and page['returned']==1 and page['has_more'] and page['next_offset']==1
        assert page['periods'][0]['ended_at_local'].startswith(date)
        assert page['periods'][0]['started_at_local'][:10]!=date
        assert page['periods'][0]['respiratory_rate']['average']==16
        assert page['periods'][0]['total_sleep_seconds'] is None
        next_page=json.loads((await wellness_tools.sleep({'date':date,'limit':1,'offset':1},ctx)).content)
        assert not next_page['has_more'] and next_page['next_offset'] is None
        for args in ({'owner_id':'someone-else'},{'date':'yesterday'},{'days':True},{'limit':0},{'offset':-1}):
            assert (await wellness_tools.sleep(args,ctx)).is_error
        share=Share(family_id=user.family_id,resource_type='conversation',resource_id=conv.id,owner_id=user.id,target_user_id=None)
        db.add(share);await db.commit()
        assert (await wellness_tools.sleep({},ctx)).is_error
        await db.delete(share);await db.delete(conv);await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[True,False])
async def test_chat_turn_delivers_and_executes_sleep_tool_or_explains_disabled_group(anna,tmp_path,monkeypatch,enabled):
    from app.services import chat_service
    from app.providers.base import ProviderEvent
    from app.providers.mock_provider import MockProvider
    monkeypatch.setattr(module,'enabled',lambda:True)
    archive=HomePulseStore(tmp_path/'private');monkeypatch.setattr(wellness,'store',lambda:archive)
    async def no_rag(*args,**kwargs):return []
    monkeypatch.setattr(chat_service.rag,'search',no_rag)
    calls=[]
    class SyntheticProvider(MockProvider):
        async def stream(self,model,system,messages,tools,**kwargs):
            names={tool.name for tool in tools};calls.append(names)
            if not enabled:
                assert 'allenamento_sonno' not in names
                assert 'strumenti non sono inclusi in questo turno' in system
                yield ProviderEvent('text_delta',text='Strumenti disattivati per questo turno.')
            elif messages[-1]['role']=='tool':
                output=json.loads(messages[-1]['content'][0]['content'])
                assert output['returned']==1 and output['periods'][0]['phases_30_seconds']=='1234'
                yield ProviderEvent('text_delta',text='Riepilogo letto dal server.')
            else:
                assert 'allenamento_sonno' in names and 'Per il sonno usa allenamento_sonno' in system
                yield ProviderEvent('tool_use',tool_use={'id':'synthetic-sleep-call','name':'allenamento_sonno','input':{}})
    provider=SyntheticProvider(api_key='',default_model='synthetic')
    monkeypatch.setattr(chat_service,'get_provider',lambda _:provider)
    monkeypatch.setattr(chat_service,'resolve_provider_model',lambda *args,**kwargs:('mock','synthetic'))
    async with SessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
        conv=Conversation(owner_id=user.id,family_id=user.family_id,title='Synthetic Emily sleep',provider='mock',model='synthetic');db.add(conv);await db.commit()
        now=int(time.time());archive.publish_processed(user.id,[],run(native(now-3600,now-1800)))
        chunks=[json.loads(chunk) async for chunk in chat_service.run_turn(db,user,conv,'Come ho dormito?',[],None,None,['mcp'] if enabled else [],None)]
        assert calls
        starts=[chunk for chunk in chunks if chunk.get('type')=='tool_start']
        assert [chunk['name'] for chunk in starts]==(['allenamento_sonno'] if enabled else [])
        assert any(chunk.get('type')=='done' for chunk in chunks)
        await db.delete(conv);await db.commit()
