// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import Desktop from '../src/components/Desktop'
const auth=vi.hoisted(()=>({me:{user:{id:'child-window-test',display_name:'Kid',role:'child',avatar:'K'},family_name:'Family',features:{apps:['messaggi'],messaggi:true}}}))
vi.mock('../src/lib/auth',()=>({useAuth:()=>({...auth,logout:vi.fn()})}))
vi.mock('../src/lib/api',()=>({get:vi.fn(async()=>[])}))
vi.mock('../src/lib/events',()=>({useLiveEvents:vi.fn()}))
vi.mock('../src/lib/native',()=>({wireNative:vi.fn()}))
vi.mock('../src/components/Notifications',()=>({default:()=>null}))
vi.mock('../src/components/Approvals',()=>({default:()=>null}))
vi.mock('../src/pages/Guide',()=>({default:()=>null}))
vi.mock('../src/pages/Messages',()=>({default:()=> <div>Messaggi del bambino</div>}))
vi.mock('../src/pages/Chat',()=>({default:()=> <div>Chat del bambino</div>}))
beforeEach(()=>{localStorage.clear();window.history.replaceState(null,'','/app');auth.me.features.messaggi=true})
afterEach(cleanup)
it('opens child messaging without redirecting to chat and stays closed',async()=>{
 render(<Desktop/>);await act(async()=>{})
 fireEvent.click(within(screen.getByRole('navigation',{name:'Dock delle applicazioni'})).getByRole('button',{name:'Messaggi',exact:true}))
 await act(async()=>{})
 expect(await screen.findByText('Messaggi del bambino')).toBeTruthy()
 expect(screen.queryByText('Chat del bambino')).toBeNull()
 expect(screen.getAllByRole('region',{name:/finestra$/})).toHaveLength(1)
 fireEvent.click(screen.getByRole('button',{name:'Chiudi Messaggi'}));await act(async()=>{})
 expect(screen.queryAllByRole('region',{name:/finestra$/})).toHaveLength(0)
})
it('restores an unauthorized window without opening chat or redirecting again',async()=>{
 auth.me.features.messaggi=false
 window.history.replaceState(null,'','/messages')
 localStorage.setItem('familos:desktop:v1:child-window-test',JSON.stringify([{id:'saved',path:'/messages',x:0,y:0,width:700,height:500,mode:'float',minimized:false}]))
 render(<Desktop/>);await act(async()=>{})
 expect(screen.getByText(/Questa app non è disponibile/)).toBeTruthy()
 expect(screen.queryByText('Chat del bambino')).toBeNull()
 expect(screen.getAllByRole('region',{name:/finestra$/})).toHaveLength(1)
 fireEvent.click(screen.getByRole('button',{name:'Chiudi Messaggi'}));await act(async()=>{})
 expect(screen.queryAllByRole('region',{name:/finestra$/})).toHaveLength(0)
})
