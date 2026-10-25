// @vitest-environment node
import {it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
const runtime=readFileSync(new URL('../src/games/runtime.js',import.meta.url),'utf8')
async function loaded(files:Record<string,string>,libraries:string[]=[], inspect?:(scope:any)=>void) {
 const urls:string[]=[]
 const nodes:any={project:{textContent:JSON.stringify({runtime:'javascript',entry:'main.js',files,libraries})},status:{},game:{},'game-root':{}}
 const scope:any={setTimeout,clearTimeout,parent:{postMessage(){}},addEventListener(){},console:{log(){},warn(){},error(){}},TextEncoder,Map,Set,Blob,URL,esbuild:{initialize:async()=>{},build:async()=>{throw new Error('Test stops before executing game')}}}
 scope.window=scope
 scope.document={getElementById:(id:string)=>nodes[id],createElement:()=>({}),head:{append:(s:any)=>{urls.push(s.src);if(s.src.includes('phaser'))scope.Phaser={Game:class {config:any;constructor(config:any){this.config=config}}};s.onload()}}}
 await runInNewContext(runtime,scope)
 inspect?.(scope)
 return urls
}
it('loads Phaser before compiling legacy projects that omit libraries',async()=>{
 const urls=await loaded({'main.js':'new Phaser.Game({})'})
 expect(urls[0]).toBe('https://cdn.jsdelivr.net/npm/phaser@3.90.0/dist/phaser.min.js')
 expect(urls[1]).toContain('esbuild-wasm')
})
it('detects Phaser in helper TypeScript files',async()=>{
 expect((await loaded({'main.js':'import "./scene"','scene.ts':'class Scene extends Phaser.Scene {}'}))[0]).toContain('phaser@3.90.0')
})
it('does not load Phaser for unrelated canvas projects',async()=>{
 expect(await loaded({'main.js':'game.canvas.getContext("2d")'})).toHaveLength(1)
})
it('loads Phaser for module syntax without a global name',async()=>{
 expect((await loaded({'main.js':'import {Game} from "phaser";new Game({})'}))[0]).toContain('phaser@3.90.0')
})
it('resolves built-in engines and nested TSX/JSON imports',async()=>{
 const resolves:any[]=[];const loads:any[]=[]
 const project={runtime:'javascript',entry:'main.ts',files:{'main.ts':'', 'scenes/view.tsx':'', 'scenes/config.json':'{}'}}
 const nodes:any={project:{textContent:JSON.stringify(project)},status:{},game:{},'game-root':{}}
 const scope:any={setTimeout,clearTimeout,parent:{postMessage(){}},addEventListener(){},console:{log(){},warn(){},error(){}},TextEncoder,Map,Set,Blob,URL,esbuild:{initialize:async()=>{},build:async(options:any)=>{
  options.plugins[0].setup({onResolve:(_:any,fn:any)=>resolves.push(fn),onLoad:(filter:any,fn:any)=>loads.push({filter,fn})})
  throw new Error('Stop before execution')
 }}}
 scope.window=scope
 scope.document={getElementById:(id:string)=>nodes[id],createElement:()=>({}),head:{append:(s:any)=>s.onload()}}
 await runInNewContext(runtime,scope)
 const resolve=resolves[0]
 expect(resolve({path:'phaser'})).toEqual({path:'phaser',namespace:'builtin'})
 expect(loads.find(x=>x.filter.namespace==='builtin').fn().contents).toContain('window.Phaser')
 expect(resolve({path:'three'})).toEqual({path:'https://cdn.jsdelivr.net/npm/three@0.174.0/build/three.module.js',external:true})
 expect(resolve({path:'./view',resolveDir:'/scenes'}).path).toBe('scenes/view.tsx')
 expect(resolve({path:'./config',resolveDir:'/scenes'}).path).toBe('scenes/config.json')
 expect(()=>resolve({path:'https://untrusted.example/code.js'})).toThrow('CDN non consentito')
 expect(()=>resolve({path:'unknown-package'})).toThrow('File o dipendenza mancante')
})

it('repairs a canvas mistakenly used as Phaser parent without mutating the project',async()=>{
 await loaded({'main.js':'new Phaser.Game(config)'},[],scope=>{
  const config={parent:'game',type:1};const instance=new scope.Phaser.Game(config)
  expect(instance.config.parent).toBe(scope.game.root)
  expect(instance.config.canvas).toBe(scope.game.canvas)
  expect(config).toEqual({parent:'game',type:1})
 })
})
