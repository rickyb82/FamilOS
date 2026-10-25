// @vitest-environment jsdom
import React from 'react'
import { vi, it, expect } from 'vitest'
import { render, screen, act, cleanup } from '@testing-library/react'
import ArtifactPanel, { type ArtifactData } from '../src/components/ArtifactPanel'
vi.mock('../src/lib/api', () => ({ get: vi.fn(async () => ({ history: [], version: 1 })), patch: vi.fn() }))
vi.mock('../src/components/Markdown', () => ({ default: () => null }))
it('refreshes current content without requiring a new version', async () => {
 const artifact: ArtifactData = { id:'a', title:'Code', kind:'code', language:'python', version:1, content:'original', conversation_id:'c' }
 const props = { canEdit:true, onClose:vi.fn(), onChange:vi.fn() }
 const view = render(<ArtifactPanel artifact={artifact} {...props} />)
 await act(async () => {})
 expect(screen.getByText('original')).toBeTruthy()
 view.rerender(<ArtifactPanel artifact={{...artifact, content:'updated in place'}} {...props} />)
 await act(async () => {})
 expect(screen.getByText('updated in place')).toBeTruthy()
 expect(screen.queryByText('original')).toBeNull()
 cleanup()
})
