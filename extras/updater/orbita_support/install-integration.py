#!/usr/bin/env python3
import os,shutil
from pathlib import Path
ROOT=Path('/opt/orbita');STAGE=Path(os.environ.get('ORBITA_UPDATE_STAGE',os.path.expanduser('~'+os.environ.get('SUDO_USER',''))+'/familos-update'))
def main():
 if os.geteuid()!=0:raise SystemExit('Eseguire con sudo')
 scripts=ROOT/'scripts';scripts.mkdir(exist_ok=True)
 for src,dst in [('productivity.py','familos-productivity.py'),('modules.json','modules.json')]:
  shutil.copyfile(STAGE/src,scripts/dst);(scripts/dst).chmod(0o644)
 entry=ROOT/'apps/familos/familos.sh'
 if 'ORBITA_MANAGED_ENTRY' not in entry.read_text():shutil.copy2(entry,entry.with_name('familos.upstream.sh'))
 entry.write_text('''#!/bin/sh
# ORBITA_MANAGED_ENTRY — gestione dello stack condiviso, nessun secondo Caddy.
set -eu
case "${1:-help}" in
 productivity|wordcraft|gridcraft|pdfcraft|deckcraft|ideacraft|mailcraft)
  if [ "$(id -u)" -eq 0 ]; then exec python3 /opt/orbita/scripts/familos-productivity.py "$@"; fi
  exec sudo python3 /opt/orbita/scripts/familos-productivity.py "$@"
  ;;
 updater)
  shift
  if [ "${1:-}" != install ]; then echo 'Uso: ./familos.sh updater install'; exit 2; fi
  exec sudo python3 /opt/orbita/apps/familos/extras/updater/install.py
  ;;
 status)
  if [ "$(id -u)" -eq 0 ]; then exec python3 /opt/orbita/scripts/familos-productivity.py productivity status; fi
  exec sudo python3 /opt/orbita/scripts/familos-productivity.py productivity status
  ;;
 *)
  echo 'Installazione gestita da Orbita.'
  echo 'Comandi: productivity up|down|status|upgrade oppure nome modulo e azione.'
  echo 'Aggiornamento framework: aggiorna.command dal Mac.'
  echo 'I comandi standalone prod/dev/update/domain/updater/stop/restore sono disabilitati in questa integrazione.'
  exit 2
  ;;
esac
''');entry.chmod(0o755)
 print('Comandi productivity integrati con Orbita. Nessun modulo avviato automaticamente.')
if __name__=='__main__':main()
