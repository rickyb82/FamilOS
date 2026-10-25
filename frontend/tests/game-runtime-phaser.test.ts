// @vitest-environment node
import {it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
const runtime=readFileSync(new URL('../src/games/runtime.js',import.meta.url),'utf8')
async function loaded(files:Record<string,string>,libraries:string[]=[]) {
 const urls:string[]=[]
 const nodes:any={project:{textContent:JSON.stringify({runtime:'javascript',entry:'main.js',files,libraries})},status:{},game:{},'game-root':{}}
 const scope:any={parent:{postMessage(){}},addEventListener(){},console:{log(){},warn(){},error(){}},TextEncoder,Map,Set,Blob,URL,esbuild:{initialize:async()=>{},build:async()=>{throw new Error('Test stops before executing game')}}}
 scope.window=scope
 scope.document={getElementById:(id:string)=>nodes[id],createElement:()=>({}),head:{append:(s:any)=>{urls.push(s.src);if(s.src.includes('phaser'))scope.Phaser={};s.onload()}}}
 await runInNewContext(runtime,scope)
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
