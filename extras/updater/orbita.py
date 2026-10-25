"""Host-side adapter for the existing Orbita Compose project."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from agent import Updater

class OrbitaUpdater(Updater):
    def __init__(self, config):
        super().__init__(config)
        self.platform = Path(config['platform']).resolve()
        if self.platform != Path('/opt/orbita') or self.root != self.platform / 'apps/familos':
            raise ValueError('Percorso Orbita non supportato')

    def status(self):
        return {**super().status(), 'adapter': 'orbita'}

    def compose(self, *args):
        with open(self.directory / 'last-compose.log', 'w') as log:
            result = subprocess.run(['docker', 'compose', *args], cwd=self.platform,
                                    stdout=subprocess.PIPE, stderr=log, text=True)
            if result.returncode:
                log.write(result.stdout)
                raise RuntimeError('Comando Compose non riuscito')
        return result.stdout.strip()

    def installed_sha(self, target):
        if target == 'core':
            status = json.loads((self.platform / 'data/install-status.json').read_text())
            return status.get('familos_revision')
        return super().installed_sha(target)

    def execute(self, script, *args, extra=None):
        env = {**os.environ, 'ORBITA_WEB_UPDATE': '1',
               **(extra or {})}
        with open(self.directory / 'last-command.log', 'w') as log:
            process = subprocess.Popen([sys.executable, str(script), *args], cwd=self.platform,
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line); log.flush()
                if line.startswith('ORBITA_PROGRESS '):
                    done, message = line[len('ORBITA_PROGRESS '):].strip().split(' ',1)
                    self.phase(message,progress=int(done),progress_kind='phases')
                elif line.startswith('Build '):self.phase('Compilazione delle immagini…')
                elif line.startswith('Salvataggio immagini'):self.phase('Salvataggio immagini per il backup…')
                elif line.startswith('Fermo servizi'):self.phase('Backup: servizi temporaneamente sospesi…',maintenance=True)
                elif line.startswith('Riavvio servizi'):self.phase('Riavvio servizi dopo il backup…')
                elif line.startswith('Cifratura GPG'):self.phase('Cifratura del backup…')
                elif line.startswith('Backup creato: '):self.phase('Backup cifrato completato; installazione in corso.',backup=line.partition(': ')[2].strip())
            if process.wait():
                raise RuntimeError('Operazione Orbita fallita')

    def install(self, body):
        target = body['target']
        offer = None if body['action'] == 'disable' else self.validate_offer(body)
        support = Path(__file__).with_name('orbita_support')
        self.phase('Preparazione aggiornamento Orbita. Il registro completo resta sulla VM.',progress=0,progress_kind='phases')
        if target == 'core':
            stage = self.directory / 'jobs' / self.state['job']['id']
            stage.mkdir(parents=True, mode=0o700)
            # Download into a separate repository: the installed app need not contain .git.
            gitdir = stage / 'source.git'
            self.run('git', 'init', '--bare', str(gitdir))
            self.run('git', '--git-dir=' + str(gitdir), 'fetch', '--depth=1',
                     'https://github.com/' + self.repos['core'] + '.git', offer['sha'])
            if self.run('git', '--git-dir=' + str(gitdir), 'rev-parse', 'FETCH_HEAD') != offer['sha']:
                raise ValueError('Commit scaricato non corrispondente')
            self.run('git', '--git-dir=' + str(gitdir), 'archive', '--format=tar.gz',
                     '--output=' + str(stage / 'source.tar.gz'), offer['sha'])
            self.phase('Sorgenti scaricati e verificati.',progress=20,progress_kind='phases')
            hashes = {}
            for path in support.iterdir():
                if path.is_file():
                    shutil.copy2(path, stage / path.name)
                    hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            (stage / 'release.json').write_text(json.dumps({'revision': offer['sha'], 'integration': hashes,
                'sha256': hashlib.sha256((stage / 'source.tar.gz').read_bytes()).hexdigest()}))
            self.phase('Build e installazione; backup manuale. Attendere la riconnessione.', maintenance=False)
            self.execute(stage / 'update-node.py', extra={'ORBITA_UPDATE_STAGE': str(stage)})
        else:
            self.phase('Build modulo e verifica servizi; backup manuale. Attendere la riconnessione.', maintenance=False)
            self.execute(support / 'productivity.py', target, 'down' if offer is None else 'upgrade',
                         extra={'ORBITA_PINNED_APP': target, 'ORBITA_PINNED_SHA': offer['sha'] if offer else ''})
        self.state['installed'][target] = {'enabled': offer is not None,
            'sha': offer['sha'] if offer else self.installed_sha(target),
            'tag': offer['tag'] if offer else self.state['installed'].get(target, {}).get('tag')}
        self.state['offers'] = {}
        self.save()
