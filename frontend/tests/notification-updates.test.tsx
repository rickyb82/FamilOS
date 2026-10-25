// @vitest-environment jsdom
import React from 'react'
import { it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Notifications from '../src/components/Notifications'
import { post } from '../src/lib/api'
const { account } = vi.hoisted(() => ({ account: {role:'admin'} }))
vi.mock('../src/lib/auth', () => ({ useAuth: () => ({me:{user:account}}) }))
vi.mock('../src/lib/events', () => ({useLiveEvents:vi.fn()}))
vi.mock('../src/lib/api', () => ({del:vi.fn(), post:vi.fn(async()=>({})), get:vi.fn(async(path:string)=>path==='/notifications'?{items:[],unread:0}:{connected:true,channel:'development',offers:{core:{newer:true,tag:'main@test'}}})}))
afterEach(()=>{cleanup();vi.clearAllMocks()})
it('shows an update badge and checks the configured channel without installing', async()=>{
 account.role='admin'
 render(<MemoryRouter><Notifications/></MemoryRouter>)
 await screen.findByLabelText('Aggiornamento disponibile')
 fireEvent.click(screen.getByTitle('Notifiche · Aggiornamento disponibile'))
 fireEvent.click(screen.getByRole('button',{name:'Cerca aggiornamenti'}))
 await waitFor(()=>expect(post).toHaveBeenCalledWith('/admin/updates/check',{target:'core',channel:'development'}))
 expect(vi.mocked(post).mock.calls.some(c=>c[0].includes('/install'))).toBe(false)
 expect(screen.getByRole('button',{name:'Vedi e installa aggiornamento'})).toBeTruthy()
})
it('does not expose server updates to a child', async()=>{
 account.role='child'
 render(<MemoryRouter><Notifications/></MemoryRouter>)
 fireEvent.click(screen.getByTitle('Notifiche'))
 expect(screen.queryByRole('button',{name:'Cerca aggiornamenti'})).toBeNull()
})
