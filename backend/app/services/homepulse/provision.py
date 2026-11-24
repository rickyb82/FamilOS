"""Import the owner's existing local archive without printing credentials.

Run inside the authenticated deployment's backend container after installation.
The input file is private and must never be committed or placed in web assets.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import uuid
from sqlalchemy import select,or_

from ...db import SessionLocal
from ...models import User,Role
from ...config import get_settings
from ..settings_store import encrypt,decrypt
from .store import HomePulseStore


async def provision(owner_name,input_path):
    async with SessionLocal() as db:
        users=(await db.execute(select(User).where(or_(User.display_name==owner_name,User.username==owner_name,User.id==owner_name),User.is_active.is_(True)))).scalars().all()
        users=[u for u in users if u.role!=Role.child]
        if len(users)!=1:raise ValueError('Il nome deve identificare esattamente un profilo adulto attivo')
        owner=users[0].id;display_name=users[0].display_name
    path=Path(input_path)
    if path.is_symlink() or not path.is_file():raise ValueError('File di importazione non valido')
    with path.open() as handle:data=json.load(handle)
    archive=HomePulseStore(get_settings().data_dir/'wellness',encrypt,decrypt)
    if data.get('settings') is not None:archive.update_settings(owner,data['settings'])
    binding=data.get('ring_binding')
    if binding:archive.set_ring(owner,binding['logical_ring_id'],binding['owner_key_hex'])
    # Stable event identities make retries safe across an interrupted installation.
    namespace=uuid.UUID('66042f33-8267-464a-9cdb-5569464322d1')
    records=data.get('history_records',[])
    events=[]
    for index,row in enumerate(records):
        events.append({'event_id':str(uuid.uuid5(namespace,'history:'+row['raw_hex'])),
                       'sequence':index,'captured_at':row['captured_at'],'kind':'ring_history',
                       'payload':{'raw_hex':row['raw_hex']}})
    events.extend(data.get('sensor_events',[]))
    for offset in range(0,len(events),500):
        archive.append(owner,'import:mac-prototype',str(uuid.uuid4()),events[offset:offset+500])
    state=archive.state(owner)
    return {'owner_name':display_name,'events':state['archive']['events'],
            'history_records':state['archive']['history_records'],
            'weekly_sessions':len(state['settings']['weekly_schedule']),
            'ring_configured':bool(state['ring_binding'])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--owner',required=True)
    parser.add_argument('--input',required=True)
    args=parser.parse_args()
    try:result=asyncio.run(provision(args.owner,args.input))
    except Exception:
        raise SystemExit('Importazione non completata: verificare profilo, file privato e configurazione. Nessuna chiave viene mostrata.') from None
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
