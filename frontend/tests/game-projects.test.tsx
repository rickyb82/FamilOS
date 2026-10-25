// @vitest-environment jsdom
import React from 'react'
import { it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import GameProjectPanel from '../src/components/GameProjectPanel'
import { gameDocument } from '../src/games/project'
import { patch } from '../src/lib/api'
vi.mock('../src/lib/api',()=>({patch:vi.fn(async(_p,body)=>({...artifact,content:body.content}))}))
const project={runtime:'javascript' as const,entry:'main.ts',files:{'main.ts':'console.log(1)','helper.ts':'export const n=2'}}
const artifact={id:'game1',title:'Game',kind:'game' as const,language:'',version:1,conversation_id:'c',content:JSON.stringify(project)}
afterEach(()=>{cleanup();vi.clearAllMocks()})
it('escapes project content and restricts network and origin access',()=>{
 const html=gameDocument({...project,files:{'main.ts':'</script><script>attack()</script>'}})
 expect(html).toContain('\\u003c/script>')
 expect(html).toContain("connect-src blob: https://cdn.jsdelivr.net https://esm.sh;")
 expect(html).toContain("form-action 'none'")
})
it('edits one file, preserves the other, and saves in the same version',async()=>{
 render(<GameProjectPanel artifact={artifact} canEdit onChange={vi.fn()}/>)
 const iframe=screen.getByTitle('Gioco: Game')
 expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin')
 fireEvent.click(screen.getByText('Codice e risorse'))
 fireEvent.change(screen.getByLabelText('Codice del file'),{target:{value:'console.log(3)'}})
 fireEvent.click(screen.getByText('Salva modifiche'))
 await waitFor(()=>expect(patch).toHaveBeenCalled())
 const args=vi.mocked(patch).mock.calls[0][1] as any
 expect(args.new_version).toBe(false)
 expect(JSON.parse(args.content).files).toEqual({'main.ts':'console.log(3)','helper.ts':'export const n=2'})
})
it('stops execution and only accepts diagnostics from its own frame',()=>{
 const ask=vi.fn()
 render(<GameProjectPanel artifact={artifact} canEdit onChange={vi.fn()} onAsk={ask}/>)
 const frame=screen.getByTitle('Gioco: Game') as HTMLIFrameElement
 fireEvent(window,new MessageEvent('message',{source:window,data:{type:'familos:game-log',level:'error',message:'forged'}}))
 fireEvent(window,new MessageEvent('message',{source:frame.contentWindow,data:{type:'familos:game-log',level:'error',message:'missing sprite'}}))
 fireEvent.click(screen.getByText('Console (1)'))
 fireEvent.click(screen.getByText('Porta gli errori in chat'))
 expect(ask.mock.calls[0][0]).toContain('missing sprite')
 expect(ask.mock.calls[0][0]).not.toContain('forged')
 fireEvent.click(screen.getByText('Ferma'))
 expect(screen.queryByTitle('Gioco: Game')).toBeNull()
})
