// @vitest-environment jsdom
import React from 'react'
import { vi, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import Games from '../src/pages/Games'
import FamilyGamePlayer from '../src/components/FamilyGamePlayer'
import { get, post, del } from '../src/lib/api'
const {user}=vi.hoisted(()=>({user:{id:'kid1',role:'child'}}))
vi.mock('../src/lib/auth',()=>({useAuth:()=>({me:{user}})}))
vi.mock('../src/lib/api',()=>({get:vi.fn(),post:vi.fn(),del:vi.fn(async()=>({}))}))
const content=JSON.stringify({runtime:'javascript',entry:'main.js',files:{'main.js':'console.log(1)'}})
afterEach(()=>{cleanup();vi.clearAllMocks()})
it('lets children play privately and request sharing without approval controls',async()=>{
 user.role='child'
 vi.mocked(get).mockImplementation(async path=>path==='/games'?[{id:'g',title:'Mio mondo',runtime:'javascript',sharing:'private',owner_id:'kid1',revision:1,updated_at:new Date().toISOString()}]:[])
 vi.mocked(post).mockResolvedValue({})
 render(<Games/>);await screen.findByText('Mio mondo')
 expect(screen.queryByText('Approva condivisione')).toBeNull()
 expect(screen.queryByText('Crea stanza famiglia')).toBeNull()
 fireEvent.click(screen.getByText('Chiedi di condividere'))
 await waitFor(()=>expect(post).toHaveBeenCalledWith('/games/g/share'))
})
it('only brokers pose messages from the current game frame and leaves on close',async()=>{
 vi.mocked(post).mockResolvedValue({players:[],self:'kid1'})
 const view=render(<FamilyGamePlayer game={{id:'g',title:'World',content}} room="r" onExit={vi.fn()}/>)
 const frame=screen.getByTitle('Gioco: World') as HTMLIFrameElement
 fireEvent(window,new MessageEvent('message',{source:window,data:{type:'familos:game-state',state:{x:999,y:0,z:0,rotation:0,action:'run'}}}))
 fireEvent(window,new MessageEvent('message',{source:frame.contentWindow,data:{type:'familos:game-state',state:{x:4,y:0,z:0,rotation:0,action:'run'}}}))
 await waitFor(()=>expect(post).toHaveBeenCalledWith('/game-rooms/r/state',{x:4,y:0,z:0,rotation:0,action:'run'}))
 expect(vi.mocked(post).mock.calls.some(([,b])=>(b as any)?.x===999)).toBe(false)
 view.unmount()
 expect(del).toHaveBeenCalledWith('/game-rooms/r/membership')
})
