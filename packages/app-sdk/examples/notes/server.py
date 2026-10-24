"""Minimal FamilOS app: relative URLs, gateway identity, atomic user-specific files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import threading
import tempfile

PAGE = '''<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Note</title>
<style>body{font:17px system-ui;background:#f4f1fb;color:#29243e;margin:32px}textarea{width:95%;height:55vh;padding:16px;border:1px solid #bdb2d2;border-radius:16px;font:inherit}button{padding:12px 25px;border:0;border-radius:12px;background:#7253ab;color:white;margin:16px 0}</style>
<h1>Le mie note</h1><p>Un’app FamilOS con salvataggio separato per utente.</p><textarea aria-label="Nota" id="note"></textarea><br><button id="save">Salva</button><span id="status" role="status"></span>
<script>const note=document.getElementById('note'), status=document.getElementById('status');
fetch('./note').then(r=>{if(!r.ok)throw Error();return r.json()}).then(v=>note.value=v.text).catch(()=>status.textContent='Caricamento non riuscito');
document.getElementById('save').onclick=async()=>{try{const r=await fetch('./note',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:note.value})});status.textContent=r.ok?'Salvato':'Salvataggio non riuscito'}catch{status.textContent='Connessione assente'}};
</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass
    def send(self,status,body,kind='application/json'):
        self.send_response(status);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(body.encode())
    def user_file(self):
        user=self.headers.get('X-FamilOS-User','')
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}',user):return None
        return Path('/app-data/notes')/(user+'.json')
    def do_GET(self):
        if self.path=='/health':return self.send(200 if os.access('/app-data/notes',os.W_OK) else 503,'{"ready":true}')
        if self.path=='/':return self.send(200,PAGE,'text/html; charset=utf-8')
        file=self.user_file()
        if self.path!='/note' or file is None:return self.send(403,'{}')
        self.send(200,file.read_text() if file.exists() else '{"text":""}')
    def do_PUT(self):
        file=self.user_file()
        if self.path!='/note' or file is None:return self.send(403,'{}')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<100000:return self.send(413,'{}')
            value=json.loads(self.rfile.read(size));text=value['text']
            if not isinstance(text,str):raise ValueError()
            with tempfile.NamedTemporaryFile(mode='w',dir=file.parent,delete=False) as stream:
                temp=Path(stream.name)
                stream.write(json.dumps({'text':text}));stream.flush();os.fsync(stream.fileno())
            temp.replace(file)
            self.send(200,'{"saved":true}')
        except (ValueError,KeyError):self.send(400,'{}')

server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler)
signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown).start())
server.serve_forever();server.server_close()
