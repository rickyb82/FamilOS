"""Portable browser game projects. No server-side execution or package installation."""
import base64
import json
import re
from urllib.parse import urlparse

MAX_SIZE = 12 * 1024 * 1024
PROJECT_SCHEMA = {"type":"object", "properties": {
    "runtime":{"type":"string","enum":["javascript","python"]},
    "entry":{"type":"string"},
    "files":{"type":"object","additionalProperties":{"type":"string"},"description":"Relative paths to UTF-8 source files. Assets may use a data: MIME;base64 URI."},
    "dependencies":{"type":"object","additionalProperties":{"type":"string"},"description":"Import names mapped to version-pinned HTTPS browser ES modules on cdn.jsdelivr.net or esm.sh."},
    "libraries":{"type":"array","items":{"type":"string","enum":["phaser"]}},
    "python_packages":{"type":"array","items":{"type":"string"},"description":"Packages included in the Pyodide distribution only."}},
    "required":["runtime","entry","files"]}

def validate_project(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict): raise ValueError('Progetto non valido')
    runtime = value.get('runtime')
    files = value.get('files')
    if runtime not in ('javascript','python') or not isinstance(files,dict) or not 1 <= len(files) <= 100:
        raise ValueError('Runtime o numero di file non valido (massimo 100)')
    for path, content in files.items():
        if not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}',path) or any(p in ('','.','..') for p in path.split('/')):
            raise ValueError('Percorso file non valido')
        if not isinstance(content,str): raise ValueError('Il contenuto dei file deve essere testo')
        if content.startswith('data:'):
            match = re.fullmatch(r'data:([\w.+-]+/[\w.+-]+);base64,([A-Za-z0-9+/=\s]*)', content)
            if not match: raise ValueError('Risorsa data URI non valida')
            base64.b64decode(match[2], validate=True)
    entry = value.get('entry')
    if entry not in files or files[entry].startswith('data:'): raise ValueError('File di avvio mancante')
    if not entry.endswith(('.py',) if runtime == 'python' else ('.js','.mjs','.ts','.tsx','.jsx')):
        raise ValueError('Estensione del file di avvio non compatibile')
    deps = value.get('dependencies',{})
    if not isinstance(deps,dict) or len(deps)>30: raise ValueError('Dipendenze non valide')
    for name,url in deps.items():
        if not isinstance(name,str) or not re.fullmatch(r'[@\w./-]{1,100}',name) or not isinstance(url,str): raise ValueError('Dipendenza non valida')
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.netloc not in ('cdn.jsdelivr.net','esm.sh') or not re.search(r'@\d+\.\d+\.\d+',parsed.path):
            raise ValueError('Usa moduli HTTPS con versione esatta su jsDelivr o esm.sh')
    libs=value.get('libraries',[])
    packages=value.get('python_packages',[])
    if not isinstance(libs,list) or any(x!='phaser' for x in libs): raise ValueError('Libreria globale non supportata')
    if not isinstance(packages,list) or len(packages)>20 or any(not isinstance(x,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}',x) for x in packages): raise ValueError('Pacchetti Python non validi')
    result=dict(runtime=runtime,entry=entry,files=files,dependencies=deps,libraries=libs,python_packages=packages)
    if len(json.dumps(result).encode())>MAX_SIZE: raise ValueError('Progetto troppo grande (massimo 12 MiB)')
    return result

def encode_project(value):
    return json.dumps(validate_project(value),ensure_ascii=False)


def standalone_html(project):
    """Portable launcher; same runtime as FamilOS, inside an opaque sandbox."""
    import html
    from pathlib import Path
    runtime_path=Path(__file__).with_name('game_runtime.js')
    if not runtime_path.exists():
        runtime_path=Path(__file__).resolve().parents[3]/'frontend/src/games/runtime.js'
    runtime=runtime_path.read_text()
    data=json.dumps(project,ensure_ascii=False).replace('<',r'\u003c')
    csp="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: https://cdn.jsdelivr.net https://esm.sh; connect-src blob: https://cdn.jsdelivr.net https://esm.sh; img-src data: blob: https://cdn.jsdelivr.net; media-src data: blob: https://cdn.jsdelivr.net; style-src 'unsafe-inline' https://cdn.jsdelivr.net; font-src data: https://cdn.jsdelivr.net; worker-src blob:; base-uri 'none'; form-action 'none'"
    doc=f'''<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{csp}"><style>html,body{{margin:0;height:100%;background:#151525;color:white;font:14px system-ui}}canvas{{display:block;max-width:100%}}#game-root{{height:100%}}</style></head><body><div id="status">Caricamento…</div><div id="game-root"><canvas id="game" width="800" height="600"></canvas></div><script id="project" type="application/json">{data}</script><script>{runtime}</script></body></html>'''
    return '<!doctype html><meta charset="utf-8"><title>Gioco FamilOS</title><iframe title="Gioco FamilOS" style="position:fixed;inset:0;width:100%;height:100%;border:0" sandbox="allow-scripts allow-pointer-lock" referrerpolicy="no-referrer" srcdoc="'+html.escape(doc,quote=True)+'"></iframe>'
