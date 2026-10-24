// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AppCatalog from '../src/pages/AppCatalog'
import InstalledApp from '../src/pages/InstalledApp'
import { appForPath } from '../src/lib/desktop'
import { get, post } from '../src/lib/api'
vi.mock('../src/lib/api',()=>({get:vi.fn(),post:vi.fn()}))
afterEach(cleanup)
beforeEach(()=>vi.resetAllMocks())

it('requires confirmation before stopping an installed app and preserves data',async()=>{
  vi.mocked(get).mockImplementation(async(path)=>path==='/installed-apps'?[{id:'notes',name:'Note',version:'1.0.0',description:'Note',enabled:true,icon:'',users:[]}]:[])
  vi.mocked(post).mockResolvedValue({status:'running'})
  render(<AppCatalog disabled={false} onChange={()=>{}}/>)
  const stop=await screen.findByRole('button',{name:'Disattiva conservando i dati'}) as HTMLButtonElement
  expect(stop.disabled).toBe(true)
  fireEvent.click(screen.getByLabelText('Confermo il backup e l’interruzione temporanea dei servizi.'))
  fireEvent.click(stop)
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/admin/app-catalog/notes/action',{action:'stop',users:[],confirm_maintenance:true}))
})
it('opens the app only in an opaque sandbox, with no referrer',async()=>{
  vi.mocked(post).mockResolvedValue({name:'Note',url:'/api/app-gateway/notes/test/'})
  render(<MemoryRouter initialEntries={['/installed/notes']}><Routes><Route path='/installed/:appId' element={<InstalledApp/>}/></Routes></MemoryRouter>)
  const frame=await screen.findByTitle('Note')
  expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-forms allow-downloads')
  expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer')
  expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
})
it('keeps different installed apps in different windows',()=>{
  expect(appForPath('/installed/notes').id).not.toBe(appForPath('/installed/sheets').id)
})
