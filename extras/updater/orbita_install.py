"""Install the root worker; invoked explicitly by the administrator, once."""
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

from agent import APPS

ROOT = Path('/opt/orbita')

def install(root):
    if root != ROOT / 'apps/familos':
        raise SystemExit('Installare dalla copia FamilOS in /opt/orbita/apps/familos')
    state=Path('/var/lib/familos-updater/state.json')
    if state.exists() and (json.loads(state.read_text()).get('job') or {}).get('status')=='running':
        raise SystemExit('Attendere il termine dell’operazione prima di reinstallare l’agente')
    directory = Path('/etc/familos-updater')
    directory.mkdir(mode=0o700, exist_ok=True)
    key = directory / 'backup-passphrase'
    if not key.exists():
        password = getpass.getpass('Password dei backup automatici (almeno 16 caratteri; conservarla fuori dal server): ')
        if len(password) < 16 or '\n' in password or password != getpass.getpass('Ripeti password: '):
            raise SystemExit('Password non valida o non coincidente')
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(password + '\n')
    if key.is_symlink() or key.stat().st_uid or key.stat().st_mode & 0o077:
        raise SystemExit('Chiave backup non protetta: richiesti proprietario root e permessi 0600')
    config_path = directory / 'config.json'
    previous = json.loads(config_path.read_text()) if config_path.exists() else {}
    if previous and previous.get('root') != str(root):
        raise SystemExit('Esiste una configurazione per un’altra installazione')
    config = {**previous, 'root': str(root), 'platform': str(ROOT), 'adapter': 'orbita',
              'state': '/var/lib/familos-updater', 'socket': '/run/familos-updater/control.sock',
              'repositories': previous.get('repositories', {'core': 'rickyb82/FamilOS', **{a: 'rickyb82/'+a for a in APPS}})}
    config_path.write_text(json.dumps(config)); config_path.chmod(0o600)
    recovery=ROOT/'data/updater-host-config.json';recovery.write_text(json.dumps(config));recovery.chmod(0o600)
    code = Path('/usr/local/lib/familos-updater'); code.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent
    for name in ('agent.py', 'orbita.py', 'catalog.py'):
        shutil.copyfile(source/name, code/name)
    shutil.copytree(source/'orbita_support', code/'orbita_support', dirs_exist_ok=True)
    shutil.copyfile(root/'packages/app-sdk/manifest.py', code/'manifest.py')
    subprocess.run(['/usr/bin/python3', '-m', 'venv', str(code/'venv')], check=True)
    subprocess.run([str(code/'venv/bin/pip'), 'install', 'pydantic>=2.7,<3'], check=True)
    socket = Path('/run/familos-updater'); socket.mkdir(mode=0o700, exist_ok=True)
    cfgpath = ROOT/'compose.familos.yaml'; old = cfgpath.read_bytes(); cfg = json.loads(old)
    backend = cfg['services']['familos-backend']
    mount = '/run/familos-updater:/run/familos-updater:ro'
    backend['volumes'] = [v for v in backend.get('volumes', []) if not (isinstance(v,str) and ':/run/familos-updater' in v)] + [mount]
    cfgpath.write_text(json.dumps(cfg, indent=2)+'\n')
    def dc(*args):
        subprocess.run(['docker','compose',*args], cwd=ROOT, check=True)
    try:
        dc('config','--quiet')
    except BaseException:
        cfgpath.write_bytes(old); raise
    shutil.copyfile(source/'orbita_support/backup-fixed.py', ROOT/'scripts/backup.py')
    # Persist the public catalogue in platform backups; no socket or backup password copied there.
    (ROOT/'data/app-catalog').mkdir(mode=0o700, exist_ok=True)
    unit = Path('/etc/systemd/system/familos-updater.service')
    unit.write_text('''[Unit]
Description=FamilOS Orbita updater and app catalogue
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=simple
ExecStartPre=/usr/bin/mkdir -p /run/familos-updater
ExecStart=/usr/local/lib/familos-updater/venv/bin/python /usr/local/lib/familos-updater/agent.py /etc/familos-updater/config.json
Restart=on-failure
RestartSec=5
UMask=0077
TimeoutStopSec=30
[Install]
WantedBy=multi-user.target
''')
    for args in (['daemon-reload'], ['enable','familos-updater'], ['restart','familos-updater']):
        subprocess.run(['systemctl', *args], check=True)
    dc('up','-d','--no-deps','--no-build','familos-backend')
    print('Agente Orbita attivato. Apri Installazione → Aggiornamenti e app.')
    print('Conserva la password backup separatamente. Dopo un ripristino su nuovo nodo reinstalla questo agente.')
