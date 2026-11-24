#!/usr/bin/env python3
"""Activate the optional built-in module without deleting personal data."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
action=sys.argv[1] if len(sys.argv)>1 else 'status'
if action not in ('up','down','status'):raise SystemExit('Uso: ./familos.sh allenamento up|down|status')
platform=Path('/opt/orbita')
if root==platform/'apps/familos' and (platform/'compose.yaml').exists():
    cwd=platform;compose=['docker','compose']
else:
    cwd=root;compose=['docker','compose','-f',str(root/'docker-compose.yml')]
code='from app.services.homepulse.module import enabled;print({"enabled":enabled()})' if action=='status' else 'from app.services.homepulse.module import configure;print(configure('+str(action=='up')+'))'
subprocess.run(compose+['exec','-T','familos-backend' if cwd==platform else 'backend','python','-c',code],cwd=cwd,check=True)
print('Modulo Allenamento aggiornato. Archivio e associazioni conservati. Ricarica FamilOS.')
