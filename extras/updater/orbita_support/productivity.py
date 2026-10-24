#!/usr/bin/env python3
"""Gestione esplicita dei moduli FamilOS nello stesso progetto Compose Orbita."""
import copy,fcntl,getpass,json,os,re,secrets,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/orbita');APP=ROOT/'apps/familos'
CATALOG=Path(__file__).with_name('modules.json')
def dc(*args,env=None,capture=False):
 return subprocess.check_output(['docker','compose',*args],cwd=ROOT,env=env,text=True) if capture else subprocess.run(['docker','compose',*args],cwd=ROOT,env=env,check=True)
def read_env(p):
 result={}
 for line in p.read_text().splitlines():
  if not line or line.lstrip().startswith('#') or '=' not in line:continue
  k,v=line.split('=',1)
  if v.startswith('"'):v=json.loads(v).replace('$$','$')
  elif v.startswith("'") and v.endswith("'"):v=v[1:-1]
  result[k]=v
 return result
def change_env(p,changes):
 lines=p.read_text().splitlines();lines=[l for l in lines if l.split('=',1)[0] not in changes]
 lines += [k+'='+json.dumps(str(v).replace('$','$$')) for k,v in changes.items()]
 tmp=p.with_name(p.name+'.orbita-tmp');tmp.write_text('\n'.join(lines)+'\n');tmp.chmod(0o600);tmp.replace(p)
def plan(cfg,catalog,apps,shas):
 cfg=copy.deepcopy(cfg)
 for name,definition in catalog['services'].items():
  app=next(a for a in apps if name in ('familos-'+a,'familos-'+a+'-web')) if any(name in ('familos-'+a,'familos-'+a+'-web') for a in apps) else None
  if app is None:continue
  service=copy.deepcopy(definition)
  assert not service.get('ports') and not service.get('network_mode'),'Il modulo non può pubblicare porte o usare rete host'
  sha=shas[app];assert re.fullmatch('[a-f0-9]{40}',sha)
  service['build']['args']['UPSTREAM_SHA']=sha
  service['image']='orbita-'+name+':'+sha[:12]
  cfg['services'][name]=service
 cfg.setdefault('secrets',{}).update(catalog['secrets'])
 return cfg
def prepare_build_cache(directory):
 for name in ('Dockerfile.cli','Dockerfile.web','Dockerfile.mailcraft'):
  path=directory/name;text=path.read_text()
  def protect(match):
   options=match.group(0)
   options=re.sub(r',sharing=[^,\s]+','',options)
   options=re.sub(r'id=cargo-registry(?=,|$)','id=cargo-registry-orbita-v2',options)
   return options+',sharing=locked'
  updated=re.sub(r'--mount=type=cache,[^\s]+',protect,text)
  if updated!=text:path.write_text(updated)

def build_services(files,services,env):
 for index,service in enumerate(services,1):
  print('Build %d/%d: %s'%(index,len(services),service),flush=True)
  cmd=['docker','compose',*[x for f in files for x in ('-f',f)],'--profile','*','build',service]
  subprocess.run(cmd,cwd=ROOT,env=env,check=True)

def main():
 if os.geteuid()!=0:raise SystemExit('Usa sudo ./familos.sh productivity up|down|status|upgrade')
 args=sys.argv[1:];catalog=json.loads(CATALOG.read_text())
 target=args[0] if args else 'productivity';action=args[1] if len(args)>1 else 'status'
 if target not in ['productivity',*catalog['apps']] or action not in ('up','down','status','upgrade','token'):raise SystemExit('Comando non supportato: productivity oppure nome modulo; up|down|status|upgrade')
 if action=='status':dc('ps','--all');return
 lock=open('/var/lib/orbita-install/migration.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if action=='token':
  private=APP/'private';private.mkdir(mode=0o700,exist_ok=True)
  token=getpass.getpass('Nuovo token GitHub in sola lettura: ').strip()
  if not token:raise SystemExit('Token vuoto: nessuna modifica')
  tokenfile=private/'github-token';tokenfile.write_text(token);tokenfile.chmod(0o600);print('Token salvato senza visualizzarlo.');return
 if not (APP/'backend/app/api/updates.py').exists():raise SystemExit('Aggiorna prima il framework con aggiorna.command')
 apps=catalog['apps'] if target=='productivity' else [target]
 cfgfile=ROOT/'compose.familos.yaml';cfg=json.loads(cfgfile.read_text())
 rootenv=ROOT/'.env';appenv=APP/'.env';originals={p:p.read_bytes() for p in (cfgfile,rootenv,appenv)}
 running=dc('ps','--services','--status','running',capture=True).splitlines()
 active=[n for n in running if n.startswith('familos-')]
 affected=[n for a in apps for n in ('familos-'+a,'familos-'+a+'-web') if n in catalog['services']]
 profiles=set(filter(None,read_env(rootenv).get('COMPOSE_PROFILES','').split(',')))
 updates={};shas={};env=None
 if action in ('up','upgrade'):
  private=APP/'private';private.mkdir(mode=0o700,exist_ok=True)
  tokenfile=private/'github-token'
  if not tokenfile.exists() or not tokenfile.read_text().strip():
   if os.environ.get('ORBITA_WEB_UPDATE')=='1':raise SystemExit('Configurare prima private/github-token sul server')
   token=getpass.getpass('Token GitHub con lettura dei repository Craft (Invio se pubblici): ').strip()
   tokenfile.write_text(token);tokenfile.chmod(0o600)
  token=tokenfile.read_text().strip()
  for a in apps:
   pinned=os.environ.get('ORBITA_PINNED_SHA','') if os.environ.get('ORBITA_PINNED_APP')==a else ''
   if pinned:
    if not re.fullmatch('[0-9a-f]{40}',pinned):raise SystemExit('Commit richiesto non valido')
    sha=pinned
   elif action=='up' and 'familos-'+a in cfg['services']:
    sha=cfg['services']['familos-'+a].get('build',{}).get('args',{}).get('UPSTREAM_SHA','')
   else:sha=''
   if not re.fullmatch('[0-9a-f]{40}',sha):
    headers={'Accept':'application/vnd.github+json','User-Agent':'Orbita-FamilOS'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request('https://api.github.com/repos/rickyb82/'+a+'/commits/HEAD',headers=headers)
    try:
     with urllib.request.urlopen(req,timeout=30) as r:sha=json.load(r)['sha']
    except Exception:raise SystemExit('Accesso al repository '+a+' non riuscito. Verifica token e permessi; token non stampato.')
   shas[a]=sha;print(a+': '+sha[:12],flush=True)
  prepare_build_cache(APP/'extras/artcraft')
  proposed=plan(cfg,catalog,apps,shas)
  env=os.environ.copy();env['DOCKER_BUILDKIT']='1';env['COMPOSE_PARALLEL_LIMIT']='1'
  # Configurazione di build separata, senza cambiare lo stack attivo.
  buildfile=ROOT/'compose.productivity-build.json';buildfile.write_text(json.dumps(proposed));buildfile.chmod(0o600)
  base=read_env(rootenv).get('COMPOSE_FILE','compose.yaml').split(':')
  files=[f for f in base if f!='compose.familos.yaml']+[buildfile.name]
  try:
   build_services(files,affected,env)
  finally:buildfile.unlink(missing_ok=True)
  cfg=proposed
  profiles.update('familos-'+a for a in apps)
 else:
  profiles.difference_update('familos-'+a for a in apps)
 for a in apps:
  enabled=action!='down';key=a.upper()
  if a=='mailcraft':
   updates.update(MAILCRAFT_URL='http://familos-mailcraft:7878' if enabled else '',MAILCRAFT_MCP_HOST='familos-mailcraft:7889' if enabled else '')
   if enabled:
    token=read_env(appenv).get('MAILCRAFT_TOKEN') or secrets.token_hex(32)
    updates['MAILCRAFT_TOKEN']=token
  else:
   port=7982+catalog['apps'].index(a)
   updates[key+'_HOST']=a+':'+str(port) if enabled else '';updates[key+'_WEB']='1' if enabled else ''
 print('Backup automatico disattivato: gestione manuale dell’amministratore.',flush=True)
 saved=Path('/var/lib/orbita-install')/('productivity-'+time.strftime('%Y%m%dT%H%M%S'));saved.mkdir(mode=0o700)
 for p,content in originals.items():(saved/('app.env' if p==appenv else p.name)).write_bytes(content)
 copied=[];mutated=False
 try:
  if active:dc('stop',*active)
  for name in ('data','private'):
   if (APP/name).exists():shutil.copytree(APP/name,saved/name,symlinks=True);copied.append(name)
  mutated=True
  cfgfile.write_text(json.dumps(cfg,indent=2)+'\n')
  rootchanges={'COMPOSE_PROFILES':','.join(sorted(profiles))}
  if 'MAILCRAFT_TOKEN' in updates:rootchanges['FAMILOS_MAILCRAFT_TOKEN']=updates['MAILCRAFT_TOKEN'];rootchanges['FAMILOS_MAILCRAFT_PUBLIC_URL']='https://familos.example.it/mailcraft'
  change_env(rootenv,rootchanges);change_env(appenv,updates)
  dc('--profile','*','config','--quiet')
  start=sorted(set(active+affected) if action!='down' else set(active)-set(affected))
  # Backend ricreato con le nuove variabili applicative; Caddy resta quello di Orbita.
  dc('up','-d','--no-build',*start)
  ready=False
  for attempt in range(30):
   try:
    for n in start:
     container=dc('ps','-q',n,capture=True).strip();assert container,'Container assente: '+n
     state=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]['State'];assert state['Running'],'Container non avviato: '+n
    if action!='down':
     for a in apps:
      port=7878 if a=='mailcraft' else 80;host='familos-mailcraft' if a=='mailcraft' else 'familos-'+a+'-web'
      dc('exec','-T','familos-backend','python','-c',"import socket;socket.create_connection(("+repr(host)+","+str(port)+"),timeout=10).close()")
    with urllib.request.urlopen('https://familos.example.it/api/health',timeout=15) as r:assert json.load(r)['status']=='ok'
    ready=True;break
   except Exception:
    time.sleep(2)
  if not ready:raise RuntimeError('Controllo avvio dei moduli non riuscito')

 except BaseException:
  print('Errore: ripristino configurazioni, dati condivisi e immagini precedenti.',flush=True)
  ids=[]
  for n in sorted(set(active+affected)):
   ids+=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project=orbita','--filter','label=com.docker.compose.service='+n],text=True).split()
  if ids:subprocess.run(['docker','stop',*ids],check=True)
  if mutated:
   for name in copied:
    (APP/name).rename(saved/('failed-'+name));shutil.copytree(saved/name,APP/name,symlinks=True)
  for p,content in originals.items():p.write_bytes(content)
  # Rimuovi solo i container dei nuovi servizi, per non lasciare orfani nel backup.
  new=[n for n in affected if n not in json.loads(originals[cfgfile])['services']]
  for n in new:
   ids=subprocess.check_output(['docker','ps','-aq','--filter','label=com.docker.compose.project=orbita','--filter','label=com.docker.compose.service='+n],text=True).split()
   if ids:subprocess.run(['docker','rm',*ids],check=True)
  if active:dc('up','-d','--no-build',*active)
  raise
 print('Operazione completata. Verifica gli editor dal desktop FamilOS. Snapshot: '+str(saved))
if __name__=='__main__':
 os.umask(0o077)
 main()
