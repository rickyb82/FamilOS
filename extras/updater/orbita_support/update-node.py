#!/usr/bin/env python3
"""Aggiornamento FamilOS integrato in Orbita; invocare tramite aggiorna.command."""
import fcntl,hashlib,json,os,re,secrets,shutil,subprocess,sys,tarfile,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/orbita'); STAGE=Path(os.environ.get('ORBITA_UPDATE_STAGE','/home/utente/familos-update'))
def run(*args,cwd=ROOT,**kw):return subprocess.run(args,cwd=cwd,check=True,**kw)
def dc(*args,**kw):return run('docker','compose',*args,**kw)
def main():
 if os.geteuid()!=0:raise SystemExit('Eseguire con sudo')
 lock=open('/var/lib/orbita-install/migration.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 for required in ('install-integration.py','productivity.py','modules.json','migrate-volumes.py','prepare-shutdown.py','backup-fixed.py'):
  assert (STAGE/required).is_file(),'File integrazione mancante: '+required
 meta=json.loads((STAGE/'release.json').read_text());rev=meta['revision']
 assert re.fullmatch('[0-9a-f]{40}',rev),'Revisione non valida'
 for n in ('install-integration.py','productivity.py','modules.json','migrate-volumes.py','prepare-shutdown.py','backup-fixed.py'):
  assert hashlib.sha256((STAGE/n).read_bytes()).hexdigest()==meta['integration'][n],'Checksum integrazione non valido: '+n
 assert hashlib.sha256((STAGE/'source.tar.gz').read_bytes()).hexdigest()==meta['sha256'],'Checksum non valido'
 run('python3',str(STAGE/'migrate-volumes.py'))
 run('python3',str(STAGE/'prepare-shutdown.py'))
 status=ROOT/'data/install-status.json';oldstatus=status.read_bytes();state=json.loads(oldstatus)
 if state.get('familos_revision') in (rev,rev[:7]):
  run('python3',str(STAGE/'install-integration.py'));print('Revisione già installata; integrazione moduli aggiornata.');return
 assert state.get('familos')=='installed','FamilOS non risulta installato'
 app=ROOT/'apps/familos'; compose=ROOT/'compose.familos.yaml';oldcompose=compose.read_bytes();cfg=json.loads(oldcompose)
 core=['familos-backend','familos-frontend','familos-sandbox','familos-searxng']
 active=subprocess.check_output(['docker','compose','ps','--services','--status','running'],cwd=ROOT,text=True).splitlines()
 names=core+sorted(set(n for n in active if n.startswith('familos-'))-set(core))
 allcfg=json.loads(subprocess.check_output(['docker','compose','--profile','*','config','--format','json'],cwd=ROOT,text=True))
 assert all(n in allcfg['services'] for n in names),'Compose incompatibile'
 saved=Path('/var/lib/orbita-install')/('familos-update-'+time.strftime('%Y%m%dT%H%M%S'))
 saved.mkdir(mode=0o700);release=saved/'release';release.mkdir()
 with tarfile.open(STAGE/'source.tar.gz') as t:
  for member in t.getmembers():
   p=Path(member.name)
   assert not p.is_absolute() and '..' not in p.parts and (member.isfile() or member.isdir()),'Archivio non valido'
   assert p.parts[0] not in ('data','private','.env','.git'),'Archivio contiene dati locali'
   member.uid=member.gid=0;member.uname=member.gname='root';t.extract(member,release)
 # Un nuovo layout richiede prima l'aggiornamento dell'adattatore Orbita.
 expected='4ec54eb75b7b0616d5c87f9a8fe6c85bff0da2ec0c161d8cf55bf57c64d2b7c4'
 assert hashlib.sha256((release/'docker-compose.yml').read_bytes()).hexdigest()==expected,'Compose upstream cambiato: serve adeguare integrazione Orbita prima di procedere'
 (saved/'compose.previous.json').write_bytes(oldcompose);(saved/'status.previous.json').write_bytes(oldstatus)
 for name,context,dockerfile in [('familos-backend',release,'backend/Dockerfile'),('familos-frontend',release/'frontend','Dockerfile'),('familos-sandbox',release/'sandbox','Dockerfile')]:
  print('Build '+name,flush=True)
  run('docker','build','-t','orbita-'+name+':'+rev[:12],'-f',str(context/dockerfile),str(context))
 # Browser isolato: adattamento esplicito del layout upstream verificato sopra.
 browser='familos-browser'
 run('docker','build','-t','orbita-familos-browser:'+rev[:12],str(release/'extras/browser'))
 env=cfg['services']['familos-backend'].get('environment',{})
 if isinstance(env,list):env=dict(v.split('=',1) for v in env if '=' in v)
 token=env.get('BROWSER_WORKER_TOKEN') or secrets.token_hex(32)
 env.update(BROWSER_WORKER_TOKEN=token,BROWSER_WORKER_URL='http://familos-browser:8080')
 cfg['services']['familos-backend']['environment']=env
 network=cfg['services']['familos-backend'].get('networks',{})
 if isinstance(network,list):network={n:{} for n in network}
 network['familos_browser']={};cfg['services']['familos-backend']['networks']=network
 cfg.setdefault('networks',{})['familos_browser']={'internal':True}
 cfg['services'][browser]={'image':'orbita-familos-browser:'+rev[:12], 'restart':'unless-stopped',
  'init':True,'read_only':True,'user':'10001:10001','networks':['familos_browser'],
  'environment':{'BROWSER_WORKER_TOKEN':token,'BROWSER_BROKER_URL':'http://familos-backend:8000/api/browser/gateway'},
  'tmpfs':['/tmp:size=512m,uid=10001,gid=10001,mode=1777'],'shm_size':'512m',
  'security_opt':['no-new-privileges:true','seccomp=./apps/familos/extras/browser/seccomp_profile.json'],
  'cap_drop':['ALL'],'pids_limit':512,'mem_limit':'2g','cpus':2.0,'expose':['8080']}
 print('ORBITA_PROGRESS 40 Immagini compilate.',flush=True)
 dc('config','--quiet')
 print('Backup automatico disattivato: gestione manuale dell’amministratore.',flush=True)
 # Snapshot coerente aggiuntivo per rollback automatico, protetto root:0700.
 original_moved=False;new_active=False
 try:
  print('ORBITA_PROGRESS 60 Preparazione completata; riavvio servizi.',flush=True)
  dc('stop',*names)
  app.rename(saved/'previous-app');original_moved=True
  for name in ('data','private','.env'):
   origin=saved/'previous-app'/name
   if origin.exists():
    if origin.is_dir():shutil.copytree(origin,release/name,symlinks=True)
    else:shutil.copy2(origin,release/name)
  # Configurazioni locali dei servizi conservate.
  for name in ('mcp_servers.json','searxng/settings.yml'):
   origin=saved/'previous-app'/name
   if origin.exists():shutil.copy2(origin,release/name)
  release.rename(app);new_active=True
  for name in core[:3]:cfg['services'][name]['image']='orbita-'+name+':'+rev[:12]
  compose.write_text(json.dumps(cfg,indent=2)+'\n')
  dc('config','--quiet')
  dc('up','-d','--no-build','familos-sandbox','familos-searxng','familos-backend')
  ready=False
  for _ in range(60):
   result=subprocess.run(['docker','compose','exec','-T','familos-backend','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if result.returncode==0:ready=True;break
   time.sleep(2)
  assert ready,'Backend non pronto'
  dc('up','-d','--no-build','familos-frontend',browser,*[n for n in names if n not in core and n!=browser])
  print('ORBITA_PROGRESS 80 Servizi avviati; verifica finale.',flush=True)
  for _ in range(20):
   try:
    with urllib.request.urlopen('https://familos.example.it/api/health',timeout=5) as r:assert json.load(r)['status']=='ok'
    with urllib.request.urlopen('https://familos.example.it/',timeout=5) as r:assert r.status==200
    break
   except Exception:time.sleep(2)
  else:raise RuntimeError('Controllo HTTPS fallito')
  run('python3',str(STAGE/'install-integration.py'))
  state['familos_revision']=rev;state['familos_updated_at']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
  status.write_text(json.dumps(state,indent=2));status.chmod(0o644)
 except BaseException:
  print('Aggiornamento fallito: ripristino insieme codice, dati e immagini precedenti.',flush=True)
  if new_active:
   dc('stop',browser)
   if browser not in names:dc('rm','-f',browser)
  dc('stop',*names)
  if new_active:app.rename(saved/'failed-app')
  if original_moved:(saved/'previous-app').rename(app)
  compose.write_bytes(oldcompose);status.write_bytes(oldstatus);status.chmod(0o644)
  dc('up','-d','--no-build',*names)
  print('Ripristino avviato. Copia fallita conservata in '+str(saved));raise
 print('AGGIORNAMENTO RIUSCITO: '+rev)
 print('Backup gestito manualmente. Copia precedente per rollback: '+str(saved))
 print('Verificare login e documenti. Per il pannello web attivare una volta extras/updater/install.py con sudo.')
if __name__=='__main__':
 os.umask(0o077)
 main()
