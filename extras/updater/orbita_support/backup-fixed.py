#!/usr/bin/env python3
"""Backup consistente e cifrato dello stack. Eseguire sul nodo, non nel container."""
import datetime
import fcntl
import getpass
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '__pycache__', '.openai', '.backup.lock'}


def run(args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def inside(path, parent):
    return path == parent or parent in path.parents


def archive_filter(info):
    if any(part in EXCLUDED for part in Path(info.name).parts):
        return None
    if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
        raise RuntimeError('File speciale non supportato: ' + info.name)
    if info.issym():
        target = Path(info.linkname)
        # Link assoluti o con .. possono riferirsi al vecchio nodo.
        if target.is_absolute() or '..' in target.parts:
            raise RuntimeError('Symlink non portabile: ' + info.name)
    return info



def encrypt_interactive(archive, destination):
    # Read from the SSH terminal, never pass a password in argv or the environment.
    if not sys.stdin.isatty():
        raise RuntimeError('Backup interattivo: usare SSH con -t oppure la chiave protetta dell’agente')
    password = getpass.getpass('Password del backup GPG: ')
    if not password:
        raise RuntimeError('Password vuota: backup annullato')
    if password != getpass.getpass('Ripeti la password del backup GPG: '):
        raise RuntimeError('Le password non coincidono: backup annullato')
    if '\n' in password or '\r' in password:
        raise RuntimeError('Password non valida')
    try:
        subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase-fd', '0',
                        '--symmetric', '--cipher-algo', 'AES256', '--output', str(destination), str(archive)],
                       cwd=ROOT, check=True, text=True, input=password+'\n')
    finally:
        password = ''


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Uso: sudo python3 scripts/backup.py /percorso/backup-esterno')
    for executable in ('docker', 'gpg'):
        if not shutil.which(executable):
            raise SystemExit(executable + ' non disponibile')
    os.umask(0o077)
    output = Path(sys.argv[1]).resolve()
    if inside(output, ROOT):
        raise SystemExit('La destinazione deve essere esterna alla piattaforma')
    output.mkdir(parents=True, exist_ok=True)
    with (ROOT / '.backup.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(run(['docker', 'compose', '--profile', '*', 'config', '--format', 'json'], True))
        project = config['name']
        ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project], True).split()
        if not ids:
            raise SystemExit('Nessun container dello stack: installa e avvia la piattaforma prima del backup')
        inspect = json.loads(run(['docker', 'inspect', *ids], True))
        # I dati persistenti devono essere bind mount interni alla piattaforma.
        for container in inspect:
            service = container['Config']['Labels'].get('com.docker.compose.service')
            if service not in config['services']:
                raise SystemExit('Container orfano: risolvi la configurazione prima del backup')
            for mount in container['Mounts']:
                if (service == 'familos-backend' and mount['Type'] == 'bind' and mount['Source'] == '/run/familos-updater' and mount['Destination'] == '/run/familos-updater' and mount.get('RW') is False):
                    continue
                if mount['Type'] == 'tmpfs':
                    continue
                if (service == 'agent' and mount['Type'] == 'bind' and
                    (mount['Source'], mount['Destination']) in (
                        ('/var/run/docker.sock', '/var/run/docker.sock'),
                        ('/run/docker.sock', '/var/run/docker.sock'),
                        ('/proc', '/host/proc'))):
                    continue
                if mount['Type'] != 'bind' or not inside(Path(mount['Source']).resolve(), ROOT):
                    raise SystemExit('Dati esterni non inclusi: ' + mount['Source'] + '. Spostali in data/ o predisponi un backup dedicato.')
        running = [c['Id'] for c in inspect if c['State']['Running']]
        services = {c['Config']['Labels']['com.docker.compose.service']: c['Image'] for c in inspect}
        image_refs = set(services.values())
        for image_id in list(image_refs):
            image = json.loads(run(['docker', 'image', 'inspect', image_id], True))[0]
            image_refs.update(image.get('RepoTags') or [])
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        destination = output / ('orbita-' + stamp + '.tar.gz.gpg')
        if destination.exists():
            raise SystemExit('Backup già esistente: ' + str(destination))
        with tempfile.TemporaryDirectory(prefix='orbita-', dir=output) as temp:
            tmp = Path(temp)
            # Le immagini sono immutabili; salvale prima del fermo per ridurre downtime.
            print('Salvataggio immagini Docker…', flush=True)
            run(['docker', 'image', 'save', '-o', str(tmp / 'images.tar'), *sorted(image_refs)])
            manifest = {'format': 1, 'created_utc': stamp, 'project': project, 'services': services,
                        'running_services': [c['Config']['Labels']['com.docker.compose.service'] for c in inspect if c['State']['Running']]}
            (tmp / 'manifest.json').write_text(json.dumps(manifest, indent=2))
            archive = tmp / 'snapshot.tar.gz'
            try:
                print('Fermo servizi e acquisizione dati…', flush=True)
                if running:
                    # Chiudi prima l'ingresso HTTP e le connessioni lunghe.
                    ingress = [c['Id'] for c in inspect if c['Id'] in running and c['Config']['Labels'].get('com.docker.compose.service') == 'proxy']
                    others = [cid for cid in running if cid not in ingress]
                    if ingress: run(['docker', 'stop', '--timeout', '60', *ingress])
                    if others: run(['docker', 'stop', '--timeout', '90', *others])
                states = json.loads(run(['docker', 'inspect', *ids], True))
                if any(c['State']['Running'] for c in states):
                    raise RuntimeError('Un servizio è ancora attivo: backup annullato')
                # Un arresto forzato può lasciare dati applicativi incoerenti.
                bad = [dict(service=c['Config']['Labels'].get('com.docker.compose.service'), exit_code=c['State'].get('ExitCode'), oom=c['State'].get('OOMKilled')) for c in states if c['State'].get('OOMKilled') or c['State'].get('ExitCode') == 137]
                if bad:
                    (ROOT/'data/backup-stop-report.json').write_text(json.dumps(bad,indent=2))
                    raise RuntimeError('Backup annullato: arresto non regolare: '+json.dumps(bad))
                with tarfile.open(archive, 'w:gz') as tar:
                    tar.add(ROOT, arcname='platform', filter=archive_filter)
                    tar.add(tmp / 'manifest.json', arcname='manifest.json')
                    tar.add(tmp / 'images.tar', arcname='images.tar')
            finally:
                if running:
                    print('Riavvio servizi precedentemente attivi…', flush=True)
                    run(['docker', 'start', *running])
            print('Cifratura GPG: scegli una password e conservala separatamente.', flush=True)
            partial = destination.with_suffix(destination.suffix + '.partial')
            try:
                key = os.environ.get('ORBITA_BACKUP_KEY_FILE')
                options = []
                if key:
                    keypath = Path(key)
                    if keypath != Path('/etc/familos-updater/backup-passphrase') or keypath.is_symlink():
                        raise RuntimeError('Percorso chiave backup non consentito')
                    st = keypath.stat()
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077 or not keypath.read_bytes().strip():
                        raise RuntimeError('Chiave backup non protetta o vuota')
                    options = ['--batch','--pinentry-mode','loopback','--passphrase-file',str(keypath)]
                if key:
                    run(['gpg', *options, '--symmetric', '--cipher-algo', 'AES256', '--output', str(partial), str(archive)])
                else:
                    encrypt_interactive(archive, partial)
                partial.rename(destination)
            finally:
                partial.unlink(missing_ok=True)
            print('Backup creato: ' + str(destination))
            print('Esegui un ripristino di prova su un nodo isolato prima di considerarlo verificato.')

if __name__ == '__main__':
    main()
