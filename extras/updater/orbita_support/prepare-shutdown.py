#!/usr/bin/env python3
import json,os,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/opt/orbita');STAGE=Path(os.environ.get('ORBITA_UPDATE_STAGE','/home/utente/familos-update'))
def dc(*args,**kw):return subprocess.run(['docker','compose',*args],cwd=ROOT,check=True,**kw)
def prepare_mailcraft(recovery_root=Path('/var/lib/orbita-install')):
 # The old Rust server ran as PID 1, which ignores default SIGTERM handling.
 # Keep a cold recovery copy before recreating it with Docker's init process.
 cfgpath=ROOT/'compose.familos.yaml';original=cfgpath.read_bytes();cfg=json.loads(original)
 service=cfg.get('services',{}).get('familos-mailcraft')
 if not service or service.get('init') is True:return
 ids=subprocess.check_output(['docker','compose','ps','-aq','familos-mailcraft'],cwd=ROOT,text=True).split()
 if len(ids)>1:raise RuntimeError('Più container Mailcraft: migrazione manuale necessaria')
 container=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))[0] if ids else None
 mounts=[]
 if container:
  for mount in container['Mounts']:
   if not mount.get('RW') or mount['Type']=='tmpfs':continue
   source=Path(mount['Source']).resolve()
   if mount['Type']!='bind' or ROOT.resolve() not in source.parents:
    raise RuntimeError('Dati Mailcraft esterni: migrazione annullata prima dell’arresto')
   mounts.append(source)
 saved=recovery_root/('mailcraft-init-'+time.strftime('%Y%m%dT%H%M%S')+'-'+str(os.getpid()))
 saved.mkdir(mode=0o700,parents=True)
 (saved/'compose.previous.json').write_bytes(original)
 was_running=bool(container and container['State']['Running'])
 changed=False
 try:
  if was_running:
   print('Migrazione Mailcraft: arresto del vecchio container (fino a 90 secondi)…',flush=True)
   dc('stop','--timeout','90','familos-mailcraft')
  if ids:
   state=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))[0]['State']
   if state['Running']:raise RuntimeError('Mailcraft ancora attivo: migrazione annullata')
   (saved/'previous-stop.json').write_text(json.dumps({k:state.get(k) for k in ('ExitCode','OOMKilled','FinishedAt')}))
  for source in sorted(set(mounts),key=lambda p:len(p.parts)):
   destination=saved/'data'/source.relative_to(ROOT.resolve())
   if destination.exists():continue
   destination.parent.mkdir(parents=True,exist_ok=True)
   if source.is_dir():shutil.copytree(source,destination,symlinks=True)
   else:shutil.copy2(source,destination)
  service['init']=True;service['stop_grace_period']='90s'
  cfgpath.write_text(json.dumps(cfg,indent=2)+'\n');changed=True
  dc('config','--quiet')
  if was_running:dc('up','-d','--no-deps','--no-build','--pull','never','familos-mailcraft')
  elif ids:dc('create','--no-deps','--no-build','--pull','never','familos-mailcraft')
 except BaseException:
  if changed:cfgpath.write_bytes(original)
  if was_running:dc('up','-d','--no-deps','--no-build','--pull','never','familos-mailcraft')
  raise
 print('Mailcraft configurato con init. Copia di recupero (non backup verificato): '+str(saved),flush=True)

def main():
 if os.geteuid()!=0:raise SystemExit('Serve sudo')
 cfgpath=ROOT/'compose.familos.yaml';cfg=json.loads(cfgpath.read_text());backend=cfg['services']['familos-backend']
 caddy=ROOT/'proxy/Caddyfile';text=caddy.read_text();wanted=text
 if re.search(r'^\s*grace_period\s+',text,re.M):wanted=re.sub(r'(?m)^\s*grace_period\s+[^\n]+','    grace_period 10s',text)
 else:wanted=text.replace('admin 127.0.0.1:2019','admin 127.0.0.1:2019\n    grace_period 10s')
 assert 'grace_period 10s' in wanted,'Caddyfile inatteso'
 env=backend.get('environment',[])
 present=env.get('UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN')=='30' if isinstance(env,dict) else 'UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30' in env
 if wanted!=text or not present:
  saved=Path('/var/lib/orbita-install')/('shutdown-before-'+time.strftime('%Y%m%dT%H%M%S'));saved.mkdir(mode=0o700)
  shutil.copy2(caddy,saved/'Caddyfile');shutil.copy2(cfgpath,saved/'compose.familos.yaml')
  caddy.write_text(wanted)
  try:
   dc('exec','-T','proxy','caddy','validate','--config','/etc/caddy/Caddyfile','--adapter','caddyfile',timeout=30)
   dc('exec','-T','proxy','caddy','reload','--config','/etc/caddy/Caddyfile','--adapter','caddyfile',timeout=45)
  except BaseException:
   caddy.write_text(text);raise
  if not present:
   if isinstance(env,dict):env['UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN']='30'
   else:env=[v for v in env if not v.startswith('UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=')]+['UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30']
   backend['environment']=env;backend['stop_grace_period']='90s'
   # Prima ferma l'ingresso; nessuna nuova richiesta arriva al backend durante il drain.
   dc('stop','--timeout','60','proxy')
   try:
    dc('stop','--timeout','90','familos-backend')
    ids=subprocess.check_output(['docker','compose','ps','-aq','proxy','familos-backend'],cwd=ROOT,text=True).split()
    states=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
    bad=[c['Name'] for c in states if c['State'].get('OOMKilled') or c['State'].get('ExitCode')==137]
    if bad:raise RuntimeError('Arresto ancora non regolare: '+str(bad)+'. Chiudere le sessioni e verificare i log; dati non modificati.')
    cfgpath.write_text(json.dumps(cfg,indent=2)+'\n');dc('config','--quiet')
   finally:
    dc('up','-d','--no-deps','--no-build','familos-backend','proxy')
 prepare_mailcraft()
 shutil.copy2(ROOT/'scripts/backup.py',ROOT/'scripts/backup.before-shutdown-fix.py')
 shutil.copyfile(STAGE/'backup-fixed.py',ROOT/'scripts/backup.py')
 print('Spegnimento configurato: Caddy 10s, richieste backend 30s. Controlli OOM/arresto forzato mantenuti.',flush=True)
if __name__=='__main__':
 os.umask(0o077);main()
