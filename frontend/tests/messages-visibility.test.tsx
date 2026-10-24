// @vitest-environment jsdom
import React from 'react'
import { act,cleanup,render } from '@testing-library/react'
import { afterEach,expect,it,vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import Messages from '../src/pages/Messages'
import { WindowActivity } from '../src/lib/desktop'
import { post } from '../src/lib/api'
vi.mock('../src/lib/auth',()=>({useAuth:()=>({me:{user:{id:'me'},features:{messaggi:true}}})}))
vi.mock('../src/lib/events',()=>({useLiveEvents:vi.fn()}))
vi.mock('../src/lib/calls',()=>({calls:{state:{},join:vi.fn()}}))
vi.mock('../src/lib/api',()=>({post:vi.fn(async()=>({})),get:vi.fn(async(path:string)=>path.includes('/messages')?{messages:[],more:false}:[]),api:vi.fn(),ApiError:class{},patch:vi.fn(),del:vi.fn()}))
afterEach(()=>{cleanup();vi.clearAllMocks()})
it('does not mark a background conversation read until its window is focused',async()=>{
 const tree=(active:boolean)=><MemoryRouter initialEntries={['/messages?room=one']}><WindowActivity.Provider value={active}><Messages/></WindowActivity.Provider></MemoryRouter>
 const view=render(tree(false));await act(async()=>{})
 expect(post).not.toHaveBeenCalled()
 view.rerender(tree(true));await act(async()=>{})
 expect(post).toHaveBeenCalledWith('/msg/rooms/one/read')
})
