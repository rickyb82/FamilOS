"""Manifest catalogue. Only fixed Docker options; no client supplied shell/Compose."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from manifest import Manifest

ORBITA_LOCK = Path('/var/lib/orbita-install/migration.lock')
ID = re.compile(r'[a-z][a-z0-9-]{1,47}\Z')

class Catalog:
    def __init__(self, worker):
        self.worker = worker
        self.root = getattr(worker, 'platform', worker.root)
        self.orbita = hasattr(worker, 'platform')
        self.directory = self.root / 'data/app-catalog'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = self.directory / 'registry.json'
        self.compose_file = self.root / 'compose.apps.json'
        self.backend = 'familos-backend' if self.orbita else 'backend'

    def read(self):
        return json.loads(self.file.read_text()) if self.file.exists() else {}

    def save(self, apps):
        from agent import write_json
        write_json(self.file, apps)

    def validate(self, raw):
        app = Manifest.model_validate(raw)
        # Fail closed until scoped document grants and controlled Internet egress exist.
        if app.permissions.documents != 'none' or app.permissions.network != 'none' or app.permissions.children:
            raise ValueError('Questo runtime supporta dati propri, rete isolata e profili adulti. Permessi richiesti non supportati.')
        architecture = {'x86_64': 'linux/amd64', 'aarch64': 'linux/arm64'}.get(platform.machine())
        if any(architecture not in s.architectures for s in app.services.values()):
            raise ValueError('Architettura del nodo non supportata dal manifest')
        return app.model_dump()

    def validate_body(self, body):
        allowed = {'action','manifest','icon','target','confirm_maintenance','users'}
        if set(body)-allowed or body.get('confirm_maintenance') is not True:
            raise ValueError('Confermare manutenzione; parametri non validi')
        if body['action'] == 'app-install':
            manifest = self.validate(body.get('manifest'))
            if body.get('target') != manifest['id']:
                raise ValueError('ID del pacchetto non corrispondente')
            try:
                icon = base64.b64decode(body.get('icon',''), validate=True)
            except Exception:
                raise ValueError('Icona PNG non valida')
            if not 24 <= len(icon) <= 131072 or not icon.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Icona PNG richiesta, massimo 128 KiB')
            if len(body.get('users',[])) > 200 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}',x) for x in body.get('users',[])):
                raise ValueError('Utenti non validi')
        elif body['action'] not in ('app-start','app-stop','app-access') or not ID.fullmatch(body.get('target','')):
            raise ValueError('Operazione catalogo non valida')
        if not isinstance(body.get('users',[]),list) or len(body.get('users',[]))>200 or any(not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}',u) for u in body.get('users',[])):
            raise ValueError('Utenti non validi')

    def compose(self, apps):
        services, networks = {}, {}
        for app_id, item in apps.items():
            m = item['manifest']; net = 'app-' + app_id
            networks[net] = {'internal': True}
            for name, spec in m['services'].items():
                service = 'app-' + app_id + '-' + name
                volumes = []
                for volume in spec['volumes']:
                    path = self.directory / app_id / 'data' / volume['name']
                    path.mkdir(parents=True, exist_ok=True)
                    if path.is_symlink():
                        raise ValueError('Volume non valido')
                    os.chown(path, spec['uid'], spec['uid'])
                    volumes.append(str(path) + ':' + volume['mount'])
                services[service] = {'image':spec['image'], 'user': f"{spec['uid']}:{spec['uid']}",
                    'restart':'unless-stopped' if item['enabled'] else 'no', 'read_only':True,
                    'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'],
                    'pids_limit':256, 'mem_limit':f"{spec['memory_mb']}m", 'cpus':spec['cpus'],
                    'tmpfs':['/tmp:size=64m,mode=1777'], 'volumes':volumes,
                    'networks':[net], 'expose':[str(spec['port'])], 'stop_grace_period':'60s',
                    'environment':{'PORT':str(spec['port']), 'FAMILOS_APP_ID':app_id,
                                   'FAMILOS_BASE_PATH':'./'},
                    'logging':{'driver':'json-file','options':{'max-size':'5m','max-file':'2'}}}
        if networks:
            services[self.backend] = {'networks':list(networks)}
        return {'services':services,'networks':networks}

    def publish(self, apps):
        # Backend reads only routing metadata, never host credentials.
        output = self.worker.root / 'data/installed-apps.json'
        from agent import write_json
        write_json(output, apps)

    def configure_orbita(self):
        if not self.orbita:
            return
        path = self.root / '.env'
        text = path.read_text(); lines = text.splitlines()
        existing = next((x.split('=',1)[1] for x in lines if x.startswith('COMPOSE_FILE=')), 'compose.yaml:compose.familos.yaml')
        try:
            existing = json.loads(existing)
        except ValueError:
            existing = existing.strip("'\"")
        files = existing.split(':')
        if 'compose.apps.json' not in files:
            files.append('compose.apps.json')
        lines = [x for x in lines if not x.startswith('COMPOSE_FILE=')]
        path.write_text('\n'.join(lines+['COMPOSE_FILE='+json.dumps(':'.join(files))])+'\n');path.chmod(0o600)

    def execute(self, body):
        self.validate_body(body)
        w = self.worker; app_id = body['target']; apps = self.read()
        previous = json.loads(json.dumps(apps))
        old_compose = self.compose_file.read_bytes() if self.compose_file.exists() else None
        old_env = (self.root/'.env').read_bytes()
        item = apps.get(app_id)
        if body['action'] == 'app-install':
            manifest = self.validate(body['manifest'])
            if item:
                old_version = tuple(map(int, item['manifest']['version'].split('.')))
                if tuple(map(int,manifest['version'].split('.'))) <= old_version:
                    raise ValueError('Usare una versione superiore; per riattivare usare Avvia')
                if manifest['data_version'] != item['manifest']['data_version']:
                    raise ValueError('Migrazione formato dati richiesta: eseguire un aggiornamento assistito')
                if manifest['services'] != item['manifest']['services']:
                    # Images/resources may change, but persistent mounts/service identities may not.
                    if {k:v['volumes'] for k,v in manifest['services'].items()} != {k:v['volumes'] for k,v in item['manifest']['services'].items()}:
                        raise ValueError('Layout dei volumi cambiato: migrazione assistita richiesta')
            item = {'manifest':manifest,'icon':body['icon'],'users':body.get('users',[]),'enabled':True}
            apps[app_id] = item
            w.phase('Scaricamento immagini verificate per digest…')
            for spec in manifest['services'].values():
                w.run('docker','pull',spec['image'])
                image = json.loads(w.run('docker','image','inspect',spec['image']))[0]
                if image.get('Config',{}).get('Volumes'):
                    raise ValueError('Immagine con volumi anonimi: dichiarare solo volumi gestiti dal manifest')
        else:
            if not item:
                raise ValueError('App non installata')
            if body['action'] == 'app-access':item['users']=body.get('users',[])
            else:item['enabled'] = body['action'] == 'app-start'
        lockpath = ORBITA_LOCK if self.orbita else self.root/'.app-operation.lock'
        lockpath.parent.mkdir(parents=True,exist_ok=True)
        with lockpath.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.orbita:
                raise ValueError('Installazione app da manifest disponibile per Orbita; configurare l’adattatore host')
            w.phase('Backup cifrato della piattaforma prima della modifica…')
            w.execute(self.root/'scripts/backup.py','/var/backups/orbita')
            if body['action']=='app-access':
                self.save(apps);self.publish(apps);return
            services = ['app-'+app_id+'-'+s for s in item['manifest']['services']]
            w.phase('Installazione e verifica app…', maintenance=True)
            snapshot = w.directory / ('app-data-'+w.state['job']['id'])
            data = self.directory/app_id/'data'
            try:
                if app_id in previous:
                    w.compose('stop','--timeout','60',*services)
                    states = w.compose('ps','-aq',*services).split()
                    if states:
                        for c in json.loads(w.run('docker','inspect',*states)):
                            if c['State'].get('OOMKilled') or c['State'].get('ExitCode')==137:
                                raise ValueError('Arresto app non regolare: recupero richiesto')
                if data.exists():
                    w.run('cp','-a',str(data),str(snapshot))
                from agent import write_json
                write_json(self.compose_file,self.compose(apps));self.configure_orbita()
                w.compose('config','--quiet')
                w.compose('up','-d','--no-build','--no-deps',self.backend)
                if item['enabled']:
                    w.compose('up','-d','--no-build','--no-deps',*services)
                    for name,spec in item['manifest']['services'].items():
                        url = 'http://app-'+app_id+'-'+name+':'+str(spec['port'])+spec['health_path']
                        for attempt in range(30):
                            try:
                                w.compose('exec','-T',self.backend,'python','-c',
                                          'import urllib.request,sys; r=urllib.request.urlopen(sys.argv[1],timeout=3); assert r.status==200',url)
                                break
                            except RuntimeError:
                                if attempt==29: raise
                                time.sleep(2)
                else:
                    w.compose('stop','--timeout','60',*services)
                self.save(apps); self.publish(apps)
                w.phase('App pronta.' if item['enabled'] else 'App disattivata; dati conservati.', maintenance=False)
            except BaseException:
                # Remove new containers before restoring previous Compose and data.
                w.compose('stop','--timeout','60',*services)
                w.compose('rm','-f',*services)
                if snapshot.exists():
                    failed = data.with_name('failed-'+w.state['job']['id'])
                    data.rename(failed);w.run('cp','-a',str(snapshot),str(data))
                if old_compose is None:
                    self.compose_file.unlink(missing_ok=True)
                else:self.compose_file.write_bytes(old_compose)
                (self.root/'.env').write_bytes(old_env)
                if previous.get(app_id,{}).get('enabled'):
                    w.compose('up','-d','--no-build','--no-deps',*services)
                self.save(previous);self.publish(previous)
                raise
