#!/usr/bin/env python3
"""Install the private HomePulse worker into the existing Orbita project.

Run after the core updater has exited and released migration.lock. No SSH,
owner-key parsing, data pruning, unrelated container stops or global .env edits.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time
import uuid

ROOT = Path('/opt/orbita')
BACKEND = 'familos-backend'
WORKER = 'familos-homepulse-core'
IMAGE = 'orbita-familos-homepulse-core:local'
LIBRARY_SHA256 = '163e10037c93ef70bd939641e0d391441e74cb4dd339946cd0a5421b91703f21'
FIXTURE_SHA256 = 'df17304071cab238340bc36ab6c0adf481a0e7848edd3b51b42b9aa34d46286f'
_COUNT_KEYS = {'users', 'rings', 'devices', 'records', 'events', 'measurements', 'plans',
               'workouts', 'packets', 'sensor_packets', 'history_records', 'samples', 'catalog_records', 'settings', 'weekly_sessions'}


def _no_symlink(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeError('Percorso di installazione non sicuro: symlink')
    return path


def _regular(path, *, private=False):
    path = _no_symlink(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError('Richiesto un file regolare senza hardlink')
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('Il file bootstrap deve avere permessi0600')
    return path


def _private_dir(path):
    path = _no_symlink(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    os.chown(path, 0, 0)
    return path


def _library_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 10_000_000:
            raise RuntimeError('Libreria amd64 non sicura o oltre il limite verificato')
        raw = source.read(10_000_001)
    if len(raw) > 10_000_000 or hashlib.sha256(raw).hexdigest() != LIBRARY_SHA256:
        raise RuntimeError('Hash della copia amd64 diverso dalla versione verificata')
    return raw


def _atomic(path, content, *, mode=0o600, uid=0, gid=0):
    path = _no_symlink(path)
    if path.exists():
        _regular(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            os.fchmod(output.fileno(), mode)
            os.fchown(output.fileno(), uid, gid)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _environment(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return {item.split('=', 1)[0]: item.split('=', 1)[1] if '=' in item else None for item in value}
    raise RuntimeError('Formato environment backend non supportato')


def plan(configuration, app):
    """Pure Compose transformation; no Docker, library or bootstrap access."""
    result = copy.deepcopy(configuration)
    services = result.get('services', {})
    if BACKEND not in services:
        raise RuntimeError('Backend FamilOS assente dalla configurazione Orbita')
    backend = services[BACKEND]
    if backend.get('network_mode'):
        raise RuntimeError('La rete speciale del backend richiede integrazione manuale')
    for name, service in services.items():
        if name not in (BACKEND, WORKER) and 'homepulse_net' in (service.get('networks') or {}):
            raise RuntimeError('La rete privata HomePulse è già usata da un altro servizio')
    networks = result.setdefault('networks', {})
    previous_network = networks.get('homepulse_net') or {}
    if previous_network.get('external') or previous_network.get('driver', 'bridge') != 'bridge':
        raise RuntimeError('La rete HomePulse esistente non è una rete privata compatibile')
    networks['homepulse_net'] = {**previous_network, 'internal': True}
    attached = backend.get('networks')
    if attached is None or attached == [] or attached == {}:
        backend['networks'] = ['default', 'homepulse_net']
        networks.setdefault('default', {})
    elif isinstance(attached, list):
        backend['networks'] = attached if 'homepulse_net' in attached else attached + ['homepulse_net']
    elif isinstance(attached, dict):
        attached.setdefault('homepulse_net', {})
    else:
        raise RuntimeError('Formato networks backend non supportato')
    environment = _environment(backend.get('environment'))
    worker_environment = _environment(services.get(WORKER, {}).get('environment'))
    token = environment.get('HOME_PULSE_CORE_TOKEN') or worker_environment.get('HOME_PULSE_CORE_TOKEN') or secrets.token_urlsafe(32)
    if not isinstance(token, str) or not token.strip() or '${' in token or '\n' in token or '\r' in token:
        raise RuntimeError('Token servizio non risolto: configurazione manuale necessaria')
    environment.update(HOME_PULSE_CORE_URL='http://homepulse-core:8081', HOME_PULSE_CORE_TOKEN=token)
    backend['environment'] = environment
    services[WORKER] = {
        'image': IMAGE, 'build': {'context': str(app / 'extras/homepulse-core'), 'dockerfile': 'Dockerfile'},
        'platform': 'linux/amd64', 'restart': 'unless-stopped', 'init': True,
        'read_only': True, 'user': '10001:10001', 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'], 'pids_limit': 64,
        'mem_limit': '512m', 'cpus': 1.0, 'ulimits': {'core': 0},
        'tmpfs': ['/tmp:rw,noexec,nosuid,size=64m,mode=1777'],
        'volumes': [{'type': 'bind', 'source': str(app / 'private/homepulse/libappecore-compat.so'),
                     'target': '/native/libappecore-compat.so', 'read_only': True}],
        'environment': {'HOME_PULSE_CORE_TOKEN': token,
                        'HOME_PULSE_NATIVE_LIBRARY': '/native/libappecore-compat.so',
                        'HOME_PULSE_NATIVE_SHIM': '/compat/libx.so'},
        'expose': ['8081'], 'networks': {'homepulse_net': {'aliases': ['homepulse-core']}},
    }
    return result


def _compose_options(root):
    selected = {}
    envfile = root / '.env'
    if envfile.exists():
        for line in _regular(envfile).read_text().splitlines():
            if '=' not in line:
                continue
            name, value = line.split('=', 1)
            if name not in ('COMPOSE_FILE', 'COMPOSE_PROJECT_NAME'):
                continue
            if value.startswith('"'):
                value = json.loads(value).replace('$$', '$')
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            selected[name] = value
    project = selected.get('COMPOSE_PROJECT_NAME', 'orbita')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise RuntimeError('Nome progetto Compose non valido')
    names = selected.get('COMPOSE_FILE', 'compose.yaml:compose.familos.yaml').split(':')
    if 'compose.familos.yaml' not in names and str(root / 'compose.familos.yaml') not in names:
        raise RuntimeError('Lo stack esistente non include compose.familos.yaml')
    options = ['compose', '--project-directory', str(root), '--project-name', project]
    if envfile.exists():
        options += ['--env-file', str(envfile)]
    for name in names:
        path = Path(name) if Path(name).is_absolute() else root / name
        options += ['-f', str(_regular(path))]
    return options, project


def _docker(root, *arguments, input=None, timeout=60, check=True):
    # Do not inherit application secrets, remote Docker contexts or loader hooks.
    environment = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
                   'HOME': '/root', 'LANG': 'C.UTF-8'}
    result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *arguments],
                            cwd=root, env=environment, input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Comando Docker di installazione non riuscito; nessun segreto visualizzato')
    return result


def _dc(root, *arguments, **kwargs):
    options, _ = _compose_options(root)
    return _docker(root, *options, *arguments, **kwargs)


HEALTH_SCRIPT = "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5); assert r.status==200"
WORKER_PROBE = '''import json,os,sys,time,urllib.request,urllib.error
payload=json.load(sys.stdin)
def fail(reason,**details):
 print(json.dumps({'ok':False,'reason':reason,**details}));sys.exit(1)
token=os.getenv('HOME_PULSE_CORE_TOKEN')
if not token:fail('configuration')
headers={'Content-Type':'application/json','Authorization':'Bearer '+token}
health={}
for attempt in range(10):
 try:
  with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8081/health',headers=headers),timeout=2) as response: health=json.load(response)
  if health.get('configured') and health.get('library_sha256')=='163e10037c93ef70bd939641e0d391441e74cb4dd339946cd0a5421b91703f21':break
 except urllib.error.HTTPError as exc:
  try:
   with exc: health=json.loads(exc.read(4096))
  except (ValueError,TypeError):pass
 except (urllib.error.URLError,TimeoutError):pass
 time.sleep(1)
else:fail('health',configured=health.get('configured') is True,library_matches=health.get('library_sha256')=='163e10037c93ef70bd939641e0d391441e74cb4dd339946cd0a5421b91703f21')
for attempt in range(40):
 try:
  request=urllib.request.Request('http://127.0.0.1:8081/process',json.dumps(payload).encode(),headers=headers)
  with urllib.request.urlopen(request,timeout=35) as response:result=json.load(response)
  break
 except urllib.error.HTTPError as exc:
  code=exc.code
  try:
   with exc: status=json.loads(exc.read(4096)).get('status')
  except (ValueError,TypeError,AttributeError):status=None
  if code==503 and status=='busy':time.sleep(1);continue
  allowed={'busy','not_configured','timeout','native_job_failed','worker_error','unauthorized','invalid_input','result_mismatch','output_limit'}
  fail('http',http_status=code,worker_status=status if status in allowed else 'unknown')
 except (urllib.error.URLError,TimeoutError):fail('http_unavailable')
else:fail('busy_timeout')
checks={'process_true':result.get('process_true') is True,'deinit_returned':result.get('deinit_returned') is True,
 'version':result.get('version')==[3,6,2],'input_record_count':result.get('input_record_count')==16,
 'mapping_complete':result.get('mapping',{}).get('complete') is True,
 'record_identity_verified':result.get('mapping',{}).get('record_identity_verified') is True,
 'sleep_native_abi':result.get('sleep_native_abi')=='ecore-3.6.2-linux-x86_64-sleep-output-200-v1',
 'sleep_capture_complete':result.get('sleep_capture_complete') is True,'sleep_outputs_type':isinstance(result.get('sleep_outputs'),list)}
if not all(checks.values()):fail('native_contract',checks=checks)
print(json.dumps({'ok':True,'synthetic_records':16}))
'''


def _health(root):
    _dc(root, 'exec', '-T', BACKEND, 'python', '-c', HEALTH_SCRIPT, timeout=12)


def _probe(root, fixture):
    result = _dc(root, 'exec', '-T', WORKER, 'python', '-c', WORKER_PROBE,
                 input=fixture, timeout=110, check=False)
    try:
        report = json.loads(result.stdout)
        ready = report.get('ok') is True
    except (ValueError, AttributeError):
        report = {}; ready = False
    if result.returncode or not ready:
        allowed = {'configuration','health','http','http_unavailable','busy_timeout','native_contract'}
        reason = report.get('reason') if report.get('reason') in allowed else 'unknown'
        details = {'reason': reason}
        if type(report.get('http_status')) is int:details['http_status'] = report['http_status']
        for key in ('configured','library_matches'):
            if type(report.get(key)) is bool:details[key] = report[key]
        if report.get('worker_status') in ('busy','not_configured','timeout','native_job_failed','worker_error','unauthorized','invalid_input','result_mismatch','output_limit'):details['worker_status'] = report['worker_status']
        checks = report.get('checks')
        if isinstance(checks,dict):details['checks'] = {k:v for k,v in checks.items() if k in ('process_true','deinit_returned','version','input_record_count','mapping_complete','record_identity_verified','sleep_native_abi','sleep_capture_complete','sleep_outputs_type') and type(v) is bool}
        raise RuntimeError('Verifica HTTP/nativa sintetica fallita: '+json.dumps(details)+'; nessuna misura promossa')


def _bootstrap(root, source, owner):
    container = _dc(root, 'ps', '-q', BACKEND).stdout.strip()
    if not re.fullmatch(r'[a-f0-9]{12,64}', container):
        raise RuntimeError('Container backend non identificato in modo univoco')
    destination = '/tmp/homepulse-seed.json'
    _regular(source, private=True)
    _dc(root, 'exec', '-T', BACKEND, 'python', '-c',
        'import os;assert not os.path.lexists("/tmp/homepulse-seed.json"),"temporary seed path already occupied"')
    try:
        # The helper never opens/parses the input file. Docker copies this one
        # explicitly selected file; the native worker never receives it.
        _docker(root, 'cp', str(source), container + ':' + destination)
        user = json.loads(_dc(root, 'exec', '-T', BACKEND, 'python', '-c',
                              'import json,os;print(json.dumps([os.getuid(),os.getgid()]))').stdout)
        if not isinstance(user, list) or len(user) != 2 or any(type(x) is not int or x < 0 for x in user):
            raise RuntimeError('UID/GID backend non validi')
        permissions = 'import os;os.chown("/tmp/homepulse-seed.json",'+str(user[0])+','+str(user[1])+');os.chmod("/tmp/homepulse-seed.json",0o600)'
        _dc(root, 'exec', '-T', '--user', '0', BACKEND, 'python', '-c', permissions)
        result = _dc(root, 'exec', '-T', BACKEND, 'python', '-m', 'app.services.homepulse.provision',
                     '--owner', owner, '--input', destination, timeout=60)
        report = json.loads(result.stdout)
        if not isinstance(report, dict):
            raise RuntimeError('Riepilogo importazione non valido')
        counts = report.get('counts', report)
        if not isinstance(counts, dict):
            raise RuntimeError('Riepilogo importazione non valido')
        return {key: value for key, value in counts.items() if key in _COUNT_KEYS and type(value) is int and value >= 0}
    finally:
        # Removal runs even if import/permissions fail. Never print command
        # output from provisioning; only whitelisted integer counts escape.
        _dc(root, 'exec', '-T', '--user', '0', BACKEND, 'python', '-c',
            'import os;os.unlink("/tmp/homepulse-seed.json") if os.path.lexists("/tmp/homepulse-seed.json") else None', timeout=12)


def install(library, *, bootstrap=None, owner_name='Proprietario', root=ROOT, dry_run=False):
    root = _no_symlink(root)
    app = root / 'apps/familos'
    if os.geteuid() != 0 and not dry_run:
        raise RuntimeError('Serve sudo; senza privilegi è consentito soltanto --dry-run')
    if not isinstance(owner_name, str) or not owner_name.strip() or len(owner_name) > 120 or any(ord(c) < 32 for c in owner_name):
        raise ValueError('Nome proprietario non valido')
    cfgpath = _regular(root / 'compose.familos.yaml')
    source = _regular(library)
    raw = _library_bytes(source)
    seed = _regular(bootstrap, private=True) if bootstrap is not None else None
    # Only metadata is inspected for the bootstrap. Its JSON/owner key is never read here.
    original = cfgpath.read_bytes()
    context = _no_symlink(app / 'extras/homepulse-core')
    _regular(context / 'Dockerfile')
    fixture = _regular(context / 'synthetic_fixture.json').read_bytes()
    if hashlib.sha256(fixture).hexdigest() != FIXTURE_SHA256:
        raise RuntimeError('Fixture sintetica diversa da quella verificata')
    fixture_body = json.dumps({'records': json.loads(fixture)['records']})
    _compose_options(root)
    if dry_run:
        plan(json.loads(original), app)
        return {'owner_name': owner_name, 'counts': {}}
    lockpath = Path('/var/lib/orbita-install/migration.lock') if root == ROOT else root / '.migration.lock'
    _no_symlink(lockpath)
    lockpath.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(lockpath, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode) or os.fstat(lock.fileno()).st_nlink != 1:
            raise RuntimeError('Lock migrazione non sicuro')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Un’altra operazione Orbita è già in corso') from None
        # Re-read under the shared lock; never overwrite a newer core update.
        original = cfgpath.read_bytes()
        before = json.loads(original)
        proposed = plan(before, app)
        _health(root)
        recovery = _private_dir(lockpath.parent / ('wellness-' + time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]))
        _atomic(recovery / 'compose.previous.json', original)
        previous_worker = before['services'].get(WORKER)
        was_running = WORKER in _dc(root, 'ps', '--services', '--status', 'running').stdout.splitlines()
        rollback_tag = None
        if previous_worker and previous_worker.get('image') == IMAGE:
            rollback_tag = IMAGE.split(':')[0] + ':rollback-' + uuid.uuid4().hex[:12]
            _docker(root, 'image', 'tag', IMAGE, rollback_tag)
        changed = False
        worker_started = False
        backend_touched = False
        previous_library_mode = None
        destination = app / 'private/homepulse/libappecore-compat.so'
        try:
            print('HomePulse: compilazione worker privato.', flush=True)
            _docker(root, 'build', '--platform', 'linux/amd64', '-t', IMAGE, str(context), timeout=600)
            _private_dir(destination.parent)
            if destination.exists():
                if hashlib.sha256(_regular(destination).read_bytes()).hexdigest() != LIBRARY_SHA256:
                    raise RuntimeError('La libreria privata esistente richiede verifica manuale')
                info = destination.stat()
                previous_library_mode = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                os.chown(destination, 10001, 10001)
                os.chmod(destination, 0o400)
            else:
                _atomic(destination, raw, mode=0o400, uid=10001, gid=10001)
            changed = True
            _atomic(cfgpath, (json.dumps(proposed, indent=2) + '\n').encode())
            _dc(root, 'config', '--quiet')
            worker_started = True
            _dc(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', WORKER, timeout=90)
            _probe(root, fixture_body)
            print('HomePulse: test sintetico riuscito, collegamento backend.', flush=True)
            backend_touched = True
            _dc(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', BACKEND, timeout=90)
            for attempt in range(15):
                try:
                    _health(root)
                    break
                except RuntimeError:
                    if attempt == 14:
                        raise
                    time.sleep(1)
            counts = _bootstrap(root, seed, owner_name) if seed is not None else {}
            return {'owner_name': owner_name, 'counts': counts}
        except BaseException:
            print('HomePulse: ripristino della configurazione precedente; dati ed eventi conservati.', flush=True)
            problems = []

            def recover(label, action):
                try:
                    action()
                except BaseException:
                    problems.append(label)

            # Restore configuration first, even when Docker itself is failing.
            if changed:
                recover('configurazione', lambda: _atomic(cfgpath, original))
            if previous_library_mode is not None:
                def restore_library_mode():
                    uid, gid, mode = previous_library_mode
                    os.chown(destination, uid, gid)
                    os.chmod(destination, mode)
                recover('permessi libreria', restore_library_mode)
            if rollback_tag:
                recover('immagine worker', lambda: _docker(root, 'image', 'tag', rollback_tag, IMAGE))
            if worker_started and previous_worker:
                recover('worker precedente', lambda: _dc(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', WORKER, timeout=90)
                        if was_running else _dc(root, 'stop', WORKER, timeout=45))
            elif worker_started:
                def remove_owned_new_worker():
                    _, project = _compose_options(root)
                    ids = _docker(root, 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project,
                                  '--filter', 'label=com.docker.compose.service=' + WORKER).stdout.split()
                    if any(not re.fullmatch(r'[a-f0-9]{12,64}', item) for item in ids):
                        raise RuntimeError('Container worker non identificati')
                    if ids:
                        _docker(root, 'rm', '-f', *ids)
                recover('nuovo worker', remove_owned_new_worker)
            if backend_touched:
                recover('backend precedente', lambda: _dc(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', BACKEND, timeout=90))
            recover('salute backend', lambda: _health(root))
            if problems:
                raise RuntimeError('Ripristino da verificare: ' + ', '.join(problems)) from None
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--bootstrap')
    parser.add_argument('--owner-name', default='Proprietario')
    parser.add_argument('--root', type=Path, default=ROOT, help='Radice Orbita; percorso alternativo per test/dry-run')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        result = install(args.library, bootstrap=args.bootstrap, owner_name=args.owner_name, root=args.root, dry_run=args.dry_run)
    except Exception as error:
        # Subprocess output can contain application secrets: never print it.
        print('Installazione HomePulse non completata: ' + str(error) if isinstance(error, (RuntimeError, ValueError)) else
              'Installazione HomePulse non completata; verificare file privati e Docker.', file=__import__('sys').stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    os.umask(0o077)
    main()
