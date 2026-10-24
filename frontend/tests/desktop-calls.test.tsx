// @vitest-environment jsdom
import React from 'react'
import { act,render,screen } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import DesktopCalls from '../src/components/DesktopCalls'
import { calls } from '../src/lib/calls'
vi.mock('../src/lib/calls',()=>({calls:{start:vi.fn(),stop:vi.fn()}}))
vi.mock('../src/lib/api',()=>({get:vi.fn(async()=>[{id:'adult',name:'Anna'}])}))
vi.mock('../src/components/CallOverlay',()=>({default:({names}:{names:Record<string,string>})=><div>Incoming calls: {names.adult}</div>}))
it('listens for calls across desktop apps and disconnects on logout',async()=>{
 const view=render(<DesktopCalls userId="child"/>);await act(async()=>{})
 expect(calls.start).toHaveBeenCalledWith('child')
 expect(screen.getByText('Incoming calls: Anna')).toBeTruthy()
 view.unmount();expect(calls.stop).toHaveBeenCalledTimes(1)
})
