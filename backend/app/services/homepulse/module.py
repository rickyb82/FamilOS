"""Optional built-in module; disabling it preserves every personal archive."""
import json
import os
from pathlib import Path
import tempfile
from ...config import get_settings


def enabled():
    root=get_settings().data_dir
    path=root/'wellness-module.json'
    if path.exists():
        try:return json.loads(path.read_text()).get('enabled') is True
        except (ValueError,OSError):return False
    # Retain an installation explicitly provisioned before the optional switch.
    return get_settings().wellness_enabled or (root/'wellness/wellness.sqlite3').is_file()


def configure(value:bool):
    if type(value) is not bool:raise ValueError('Stato modulo non valido')
    root=get_settings().data_dir;root.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.wellness-module-',dir=root)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump({'enabled':value},stream);stream.flush();os.fsync(stream.fileno())
        os.replace(name,root/'wellness-module.json')
    finally:
        if Path(name).exists():Path(name).unlink()
    return {'enabled':value,'data_preserved':True}
