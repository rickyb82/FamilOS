import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User,MsgMember,Notification
from app.services import events


def test_presence_expires_and_multiple_tabs_remain_online(monkeypatch):
    clock=[100.0];monkeypatch.setattr(events.time,'monotonic',lambda:clock[0])
    first=events.subscribe('presence-test');second=events.subscribe('presence-test')
    try:
        assert events.online('presence-test')
        events.unsubscribe('presence-test',first)
        assert events.online('presence-test')
        clock[0]+=91
        assert not events.online('presence-test')
        events.touch('presence-test')
        assert events.online('presence-test')
    finally:
        events.unsubscribe('presence-test',second)
    assert not events.online('presence-test')

@pytest.mark.asyncio
async def test_messages_notify_online_recipient_and_respect_mute(kid,anna,monkeypatch):
    from app.services import push
    sent=[];monkeypatch.setattr(push,'send_later',lambda *args:sent.append(args))
    aid=(await anna.get('/api/auth/me')).json()['user']['id']
    kid_id=(await kid.get('/api/auth/me')).json()['user']['id']
    room=(await kid.post('/api/msg/rooms',json={'members':[aid]})).json()['id']
    q=events.subscribe(aid)
    try:
        response=await kid.post(f'/api/msg/rooms/{room}/messages',json={'text':'notification regression fixture'})
        assert response.status_code==200,response.text
        async with SessionLocal() as db:
            notices=(await db.execute(select(Notification).where(Notification.user_id==aid,Notification.text=='notification regression fixture'))).scalars().all()
            assert len(notices)==1
            assert notices[0].link==f'/messages?room={room}'
            member=await db.get(MsgMember,(room,aid));member.muted=True;await db.commit()
        delivered=[]
        while not q.empty():delivered.append(q.get_nowait())
        assert any(e['kind']=='notification' for e in delivered)
        assert len(sent)==1 and sent[0][0]==aid
        assert (await kid.post(f'/api/msg/rooms/{room}/messages',json={'text':'muted regression fixture'})).status_code==200
        assert len(sent)==1
        async with SessionLocal() as db:
            notices=(await db.execute(select(Notification).where(Notification.text=='muted regression fixture'))).scalars().all()
            assert notices==[]
    finally:
        events.unsubscribe(aid,q)
        async with SessionLocal() as db:
            member=await db.get(MsgMember,(room,aid));member.muted=False;await db.commit()
