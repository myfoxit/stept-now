import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'
import { ApprovalCard } from '@/features/inbox/components/ApprovalCard'
import type { Approval } from '@/features/inbox/api'

const approval: Approval = {
  id: 'ap1',
  run_id: 'r1',
  conversation_id: 'c1',
  agent_id: 'ag1',
  agent_name: 'Sage',
  tool_key: 'close_conversation',
  tool_input: { reason: 'resolved by AI' },
  status: 'pending',
  requested_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 3600_000).toISOString(),
  decided_by: null,
  decided_at: null,
  note: null,
  created_at: new Date().toISOString(),
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'r@stept.co', name: 'Reggie' },
    memberships: [],
    workspaceId: 'ws1',
    bootstrapped: true,
  })
})

describe('ApprovalCard', () => {
  it('submits an approve decision', async () => {
    let decideBody: { approved?: boolean } | null = null
    mockFetch({
      'POST /api/v1/w/ws1/ai/approvals/ap1/decide': (init) => {
        decideBody = JSON.parse(init!.body as string)
        return { body: { ...approval, status: 'approved' } }
      },
    })
    renderApp(<ApprovalCard approval={approval} canApprove />)
    expect(screen.getByText('Sage')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /approve/i }))
    await waitFor(() => expect(decideBody).not.toBeNull())
    expect(decideBody!).toMatchObject({ approved: true })
  })

  it('hides decision controls without ai:approve', () => {
    mockFetch({})
    renderApp(<ApprovalCard approval={approval} canApprove={false} />)
    expect(screen.getByText(/don't have permission/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
  })
})
