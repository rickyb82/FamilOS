#!/usr/bin/env python3
"""Rende esplicito il volume /etc/searxng, conservando dati e rollback."""
import hashlib,json,os,shutil,stat,subprocess,time
from pathlib import Path
ROOT=Path('/opt/orbita');SERVICE='familos-searxng'
def run(*args,capture=False):
 return subprocess.check_output(args,cwd=ROOT,text=True) if capture else subprocess.run(args,cwd=ROOT,check=True)
def dc(*args,capture=False):return run('docker','compose',*args,capture=capture)
def manifest(root):
 result={}
 for p in [root,*sorted(root.rglob('*'))]:
  s=p.lstat();entry=[stat.S_IMODE(s.st_mode),s.st_uid,s.st_gid]
  if p.is_symlink():entry+=['link',os.readlink(p)]
  elif p.is_dir():entry+=['dir']
  elif p.is_file():
   h=hashlib.sha256()
   with p.open('rb') as f:
    for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
   entry+=['file',s.st_size,h.hexdigest()]
  else:raise RuntimeError('File speciale nel volume: '+str(p))
  result[str(p.relative_to(root))]=entry
 return result
def main():
 if os.geteuid()!=0:raise SystemExit('Eseguire con sudo')
 cid=dc('ps','-aq',SERVICE,capture=True).strip()
 if not cid:raise RuntimeError('Container SearXNG non trovato')
 c=json.loads(run('docker','inspect',cid,capture=True))[0]
 labels=c['Config']['Labels'];assert labels['com.docker.compose.project']=='orbita' and labels['com.docker.compose.service']==SERVICE
 mounts=[m for m in c['Mounts'] if m['Destination']=='/etc/searxng']
 if len(mounts)!=1:raise RuntimeError('Mount /etc/searxng inatteso')
 m=mounts[0]
 if m['Type']=='bind' and Path(m['Source']).resolve().is_relative_to(ROOT):
  print('SearXNG: configurazione già inclusa nei backup.');return
 assert m['Type']=='volume','Tipo mount inatteso; nessuna modifica'
 print('Volume da migrare: '+m['Name']+' -> /etc/searxng',flush=True)
 volume=json.loads(run('docker','volume','inspect',m['Name'],capture=True))[0]
 assert volume['Driver']=='local' and not volume.get('Options'),'Volume remoto/plugin non supportato'
 source=Path(volume['Mountpoint']);assert str(source)==m['Source']
 consumers=run('docker','ps','-aq','--filter','volume='+m['Name'],capture=True).split()
 assert consumers==[c['Id']] or consumers==[c['Id'][:12]],'Volume condiviso: richiede migrazione dedicata'
 target=ROOT/'data/familos/searxng_config'
 assert not target.exists(),'Destinazione già presente: verificare eventuale migrazione precedente'
 assert shutil.disk_usage(ROOT).free>sum(p.stat().st_size for p in source.rglob('*') if p.is_file() and not p.is_symlink())*2+100*1024*1024,'Spazio insufficiente'
 cfgpath=ROOT/'compose.familos.yaml';original=cfgpath.read_bytes();cfg=json.loads(original)
 saved=Path('/var/lib/orbita-install')/('searxng-volume-'+time.strftime('%Y%m%dT%H%M%S'));saved.mkdir(mode=0o700)
 (saved/'compose.before.json').write_bytes(original)
 # Recovery riutilizza esattamente volume e immagine precedenti, senza nuovo volume anonimo.
 rollback=json.loads(original);svc=rollback['services'][SERVICE];svc['image']=c['Image']
 svc['volumes']=[v for v in svc.get('volumes',[]) if (v.get('target') if isinstance(v,dict) else v.split(':')[1])!='/etc/searxng']
 svc['volumes'].append({'type':'volume','source':'orbita_searxng_recovery','target':'/etc/searxng'})
 rollback.setdefault('volumes',{})['orbita_searxng_recovery']={'external':True,'name':m['Name']}
 (saved/'compose.recovery.json').write_text(json.dumps(rollback,indent=2))
 changed=False;was_running=c['State']['Running']
 dc('stop',SERVICE)
 try:
  target.parent.mkdir(parents=True,exist_ok=True)
  run('cp','-a',str(source),str(target))
  assert manifest(source)==manifest(target),'Verifica dati, proprietari o permessi fallita'
  (saved/'manifest.json').write_text(json.dumps(manifest(target),indent=2))
  cfg['services'][SERVICE]['volumes'].append('./data/familos/searxng_config:/etc/searxng')
  cfg['services'][SERVICE]['image']=c['Image']
  cfgpath.write_text(json.dumps(cfg,indent=2)+'\n');changed=True
  dc('config','--quiet')
  if was_running:
   dc('up','-d','--no-deps','--no-build','--pull','never','--force-recreate',SERVICE)
   ready=False
   for _ in range(30):
    r=subprocess.run(['docker','compose','exec','-T','familos-backend','python','-c',"import urllib.request;urllib.request.urlopen('http://familos-searxng:8080',timeout=3)"],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if r.returncode==0:ready=True;break
    time.sleep(2)
   assert ready,'SearXNG non risponde dopo la migrazione'
   new=json.loads(run('docker','inspect',dc('ps','-q',SERVICE,capture=True).strip(),capture=True))[0]
   assert any(x['Destination']=='/etc/searxng' and x['Type']=='bind' and x['Source']==str(target) for x in new['Mounts'])
  else:
   dc('create','--no-build','--no-deps','--force-recreate',SERVICE)
 except BaseException:
  if changed:
   cfgpath.write_text(json.dumps(rollback,indent=2)+'\n')
   if was_running:dc('up','-d','--no-build','--no-deps','--pull','never','--force-recreate',SERVICE)
   else:dc('create','--no-build','--no-deps','--force-recreate',SERVICE)
  elif was_running:run('docker','start',c['Id'])
  print('Migrazione fallita. Volume originale conservato; diagnostica: '+str(saved));raise
 print('PASS: configurazione SearXNG copiata e verificata; volume originale conservato. Backup completo ora da eseguire.')
if __name__=='__main__':
 os.umask(0o077);main()
