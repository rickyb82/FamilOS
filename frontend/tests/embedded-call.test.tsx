// @vitest-environment jsdom
import React from 'react'
import { act,cleanup,render,fireEvent,screen } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import CallOverlay from '../src/components/CallOverlay'
const model=vi.hoisted(()=>({listener:null as any,state:{roomId:'room',video:true,status:'active',peers:[],local:null,incoming:null,startedAt:0} as any,accept:vi.fn()}))
vi.mock('../src/lib/calls',()=>({calls:{get state(){return model.state},subscribe:(f:any)=>{model.listener=f;return ()=>{}},accept:model.accept,clearError:vi.fn()}}))
afterEach(cleanup)
it('renders video inside its Messages container and attaches video arriving after audio',async()=>{
 const tracks:any[]=[]
 const stream={getVideoTracks:()=>tracks} as unknown as MediaStream
 model.state={...model.state,status:'active',incoming:null,peers:[{id:'other',stream,state:'connected'}]}
 const view=render(<section data-testid="messages"><CallOverlay names={{other:'Anna'}} roomId="room"/></section>)
 const host=screen.getByTestId('messages')
 expect(host.querySelector('.call-embedded')).toBeTruthy()
 expect(host.querySelector('audio')?.srcObject).toBe(stream)
 tracks.push({enabled:true,readyState:'live'})
 act(()=>model.listener({...model.state,peers:[...model.state.peers]}))
 expect(host.querySelector('video')?.srcObject).toBe(stream)
 view.unmount()
})
it('incoming answer opens the Messages room and accepts in the user gesture',()=>{
 model.state={...model.state,status:'idle',incoming:{roomId:'incoming',video:true,fromName:'Anna'}}
 const open=vi.fn()
 render(<CallOverlay names={{}} mode="incoming" onAnswer={open}/>)
 fireEvent.click(screen.getByTitle('Rispondi'))
 expect(open).toHaveBeenCalledWith('incoming');expect(model.accept).toHaveBeenCalled()
 expect(document.querySelector('.call-overlay')).toBeNull()
})
