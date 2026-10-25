import runtime from './runtime.js?raw'
export interface GameProject {runtime:'javascript'|'python';entry:string;files:Record<string,string>;dependencies?:Record<string,string>;libraries?:string[];python_packages?:string[]}
export function parseProject(content:string):GameProject {
 const p=JSON.parse(content)
 if(!['javascript','python'].includes(p.runtime)||!p.files||typeof p.files[p.entry]!=='string')throw new Error('Manifest del gioco non valido')
 return p
}
export function gameDocument(p:GameProject) {
 const data=JSON.stringify(p).replace(/</g,'\\u003c')
 return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: https://cdn.jsdelivr.net https://esm.sh; connect-src blob: https://cdn.jsdelivr.net https://esm.sh; img-src data: blob: https://cdn.jsdelivr.net; media-src data: blob: https://cdn.jsdelivr.net; style-src 'unsafe-inline' https://cdn.jsdelivr.net; font-src data: https://cdn.jsdelivr.net; worker-src blob:; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;background:#151525;color:white;font:14px system-ui;height:100%;overflow:hidden}#game-root{width:100%;height:100%}canvas{display:block;max-width:100%}#status{position:absolute;inset:16px;pointer-events:none}</style></head><body><div id="status">Caricamento…</div><div id="game-root"><canvas id="game" width="800" height="600"></canvas></div><script id="project" type="application/json">${data}</script><script>${runtime}</script></body></html>`
}
