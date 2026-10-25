(async () => {
  const project = JSON.parse(document.getElementById('project').textContent);
  const report = (level, message) => parent.postMessage({type:'familos:game-log',level,message:String(message).slice(0,4000)}, '*');
  const status = document.getElementById('status');
  addEventListener('error', e => report('error', e.message + ' ' + (e.filename || '') + ':' + e.lineno));
  addEventListener('unhandledrejection', e => report('error', e.reason?.stack || e.reason));
  for (const level of ['log','warn','error']) {
    const original = console[level].bind(console);
    console[level] = (...args) => { original(...args); report(level,args.map(x=>String(x)).join(' ')); };
  }
  const bytes = value => value.startsWith('data:') ? Uint8Array.from(atob(value.split(',')[1]),c=>c.charCodeAt(0)) : new TextEncoder().encode(value);
  const mime = path => ({svg:'image/svg+xml',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',mp3:'audio/mpeg',wav:'audio/wav',ogg:'audio/ogg',json:'application/json',css:'text/css'}[path.split('.').pop()] || 'application/octet-stream');
  const assets = new Map();
  window.game = { root:document.getElementById('game-root'), canvas:document.getElementById('game'), asset(path) {
    if (!(path in project.files)) throw new Error('Risorsa mancante: '+path);
    if (!assets.has(path)) assets.set(path, URL.createObjectURL(new Blob([bytes(project.files[path])],{type:mime(path)})));
    return assets.get(path);
  }};
  const peerListeners = new Set();
  window.game.multiplayer = {
    publish(state) { parent.postMessage({type:'familos:game-state',state}, '*'); },
    onPeers(fn) { peerListeners.add(fn); return () => peerListeners.delete(fn); }
  };
  addEventListener('message', e => {
    if(e.source !== parent || e.data?.type !== 'familos:game-peers' || !Array.isArray(e.data.players)) return;
    for(const fn of peerListeners) { try { fn(e.data.players); } catch(err) { report('error',err.message); } }
  });
  const script = url => new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=url;s.onload=resolve;s.onerror=()=>reject(new Error('Libreria non raggiungibile: '+url));document.head.append(s)});
  try {
    if (project.runtime === 'python') {
      status.textContent='Caricamento Python…';
      await script('https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js');
      const py = await loadPyodide({indexURL:'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/'});
      py.setStdout({batched:s=>report('log',s)}); py.setStderr({batched:s=>report('error',s)});
      py.FS.mkdirTree('/game');
      for (const [path,value] of Object.entries(project.files)) {
        const full='/game/'+path;py.FS.mkdirTree(full.slice(0,full.lastIndexOf('/')));py.FS.writeFile(full,bytes(value));
      }
      await py.loadPackage(project.python_packages || []);
      await py.runPythonAsync("import sys, os\nos.chdir('/game')\nsys.path.insert(0, '/game')");
      status.hidden=true;
      await py.runPythonAsync(project.files[project.entry],{filename:'/game/'+project.entry});
    } else {
      status.textContent='Preparazione progetto…';
      if ((project.libraries || []).includes('phaser')) await script('https://cdn.jsdelivr.net/npm/phaser@3.90.0/dist/phaser.min.js');
      await script('https://cdn.jsdelivr.net/npm/esbuild-wasm@0.25.0/lib/browser.min.js');
      await esbuild.initialize({wasmURL:'https://cdn.jsdelivr.net/npm/esbuild-wasm@0.25.0/esbuild.wasm',worker:false});
      const normalize = path => {
        const parts=[];for(const p of path.split('/')) {if(p==='..')parts.pop();else if(p && p!=='.')parts.push(p)}return parts.join('/');
      };
      const result = await esbuild.build({entryPoints:[project.entry],bundle:true,write:false,format:'esm',platform:'browser',outdir:'/out',plugins:[{
        name:'project',setup(build) {
          build.onResolve({filter:/.*/}, args => {
            const dependency=project.dependencies?.[args.path];
            if (dependency) return {path:dependency,external:true};
            if (/^https:\/\//.test(args.path)) {
              const url=new URL(args.path);
              if(!['cdn.jsdelivr.net','esm.sh'].includes(url.host))throw new Error('CDN non consentito');
              return {path:args.path,external:true};
            }
            const path=normalize(args.kind==='entry-point'?args.path:(args.path.startsWith('.')?args.resolveDir+'/'+args.path:args.path));
            const found=[path,path+'.ts',path+'.js',path+'/index.ts',path+'/index.js'].find(x=>x in project.files);
            if (!found) throw new Error('File o dipendenza mancante: '+args.path);
            return {path:found,namespace:'project'};
          });
          build.onLoad({filter:/.*/,namespace:'project'}, args => {
            const ext=args.path.split('.').pop();
            const loader={js:'js',mjs:'js',ts:'ts',jsx:'jsx',tsx:'tsx',json:'json',css:'css'}[ext] || 'dataurl';
            return {contents:bytes(project.files[args.path]),loader,resolveDir:'/'+(args.path.includes('/')?args.path.slice(0,args.path.lastIndexOf('/')):'')};
          });
        }
      }]});
      for (const file of result.outputFiles) if(file.path.endsWith('.css')) {const style=document.createElement('style');style.textContent=file.text;document.head.append(style)}
      const js=result.outputFiles.find(f=>f.path.endsWith('.js'));
      if(!js) throw new Error('Nessun codice eseguibile generato');
      const map=document.createElement('script');map.type='importmap';map.textContent=JSON.stringify({imports:project.dependencies||{}});document.head.append(map);
      status.hidden=true;
      await import(URL.createObjectURL(new Blob([js.text],{type:'text/javascript'})));
    }
    report('ready','Gioco avviato');
  } catch(e) {
    const details=Array.isArray(e.errors) ? e.errors.map(error => {
      const loc=error.location;
      return (loc ? `${loc.file}:${loc.line}:${loc.column} · ` : '') + error.text;
    }).join('\n') : '';
    status.hidden=false;
    status.textContent='Avvio non riuscito: '+(details || e.message);
    report('error',details || e.stack || e.message);
  }
})();
