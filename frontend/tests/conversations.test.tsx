// @vitest-environment jsdom
import React from 'react'
import {it,expect,vi,afterEach} from 'vitest'
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import Conversations from '../src/pages/Conversations'
import {get,del} from '../src/lib/api'
vi.mock('../src/lib/api',()=>({get:vi.fn(),del:vi.fn(async()=>({}))}))
vi.mock('../src/lib/auth',()=>({useAuth:()=>({me:{user:{id:'me'}}})}))
afterEach(()=>{cleanup();vi.restoreAllMocks()})
it('shows older chats, searches and requires confirmation before deletion',async()=>{
 const rows=Array.from({length:5},(_,i)=>({id:String(i),title:'Chat '+i,owner_id:i===4?'other':'me',updated_at:'2026-10-25T10:00:00'}))
 vi.mocked(get).mockResolvedValue(rows)
 render(<MemoryRouter><Conversations/></MemoryRouter>)
 expect(await screen.findByText('Chat 4')).toBeTruthy()
 expect(screen.getAllByText('Elimina')).toHaveLength(4)
 fireEvent.change(screen.getByPlaceholderText('Cerca nelle tue chat…'),{target:{value:'Chat 0'}})
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false)
 fireEvent.click(screen.getByText('Elimina'))
 expect(del).not.toHaveBeenCalled()
 confirm.mockReturnValue(true)
 vi.mocked(get).mockResolvedValue(rows.slice(1))
 fireEvent.click(screen.getByText('Elimina'))
 await waitFor(()=>expect(del).toHaveBeenCalledWith('/chat/conversations/0'))
})
