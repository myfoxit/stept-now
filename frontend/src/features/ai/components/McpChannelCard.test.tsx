import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import type { Agent, ToolConfig } from '../api'
import { seedAuth } from '../test-utils'
import { deriveMcpExposure, McpChannelCard } from './McpChannelCard'

const ISO = '2026-07-31T10:00:00Z'
const PERMS = ['ai:read', 'ai:manage', 'ai:approve', 'knowledge:read', 'apikeys:manage']

const BASE_SETTINGS = {
  retrieval: { enabled: true, k: 6, source_ids: null },
  handoff_message: 'Over to you',
  guardrails: { max_tool_calls: 8, require_citations: false },
  page_control: { enabled: false, allow_actions: false },
}

const ACTIONS = [
  { id: 'act1', name: 'Create ticket' },
  { id: 'act2', name: 'Get order status' },
  { id: 'act3', name: 'Delete user' },
]

const TOOLS: ToolConfig[] = [
  { key: 'search_knowledge', policy: 'auto' },
  { key: 'close_conversation', policy: 'auto' },
  { key: 'action:act1', policy: 'require_approval' },
  { key: 'action:act2', policy: 'auto' },
  { key: 'action:act3', policy: 'disabled' },
]

function makeAgent(overrides: Record<string, unknown> = {}): Agent {
  return {
    id: 'a1',
    name: 'Sage',
    description: null,
    avatar_emoji: '🤖',
    status: 'live',
    model_ref: null,
    system_prompt: 'You help.',
    temperature: null,
    settings: { ...BASE_SETTINGS },
    tools: TOOLS,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  } as Agent
}

function baseRoutes() {
  return {
    'GET /api/v1/w/w1/api-keys': () => ({ body: [] as unknown[] }),
    'GET /api/v1/w/w1/ai/actions': () => ({ body: ACTIONS }),
  }
}

describe('deriveMcpExposure', () => {
  it('classifies tools with the read-prefix heuristic and skips hidden ones', () => {
    const { reads, writes } = deriveMcpExposure(TOOLS, ACTIONS)
    expect(reads).toEqual(['ask_agent', 'search_knowledge', 'action_get_order_status'])
    expect(writes).toEqual(['action_create_ticket'])
  })

  it('slugifies action names the way the backend endpoint does', () => {
    const actions = [
      { id: 'a1', name: 'Get  Order__Status' },
      { id: 'a2', name: '  Refund Order!  ' },
      { id: 'a3', name: '***' },
    ]
    const tools = actions.map((a) => ({ key: `action:${a.id}`, policy: 'auto' as const }))
    const { reads, writes } = deriveMcpExposure(tools, actions)
    // Underscores already in the name survive; runs of other characters collapse
    // to one; a name with nothing usable falls back to "action".
    expect(reads).toContain('action_get_order__status')
    expect(writes).toEqual(['action_refund_order', 'action_action'])
  })
})

describe('McpChannelCard', () => {
  it('enabling the channel PATCHes settings.mcp and preserves sibling settings keys', async () => {
    seedAuth(PERMS)
    let patchBody: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes(),
      'PATCH /api/v1/w/w1/ai/agents/a1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: makeAgent() }
      },
    })
    renderApp(<McpChannelCard agent={makeAgent()} />)

    await userEvent.click(screen.getByRole('switch', { name: 'Enable MCP channel' }))

    await waitFor(() =>
      expect(patchBody).toEqual({
        settings: {
          ...BASE_SETTINGS,
          mcp: { enabled: true, approval_mode: 'ask_in_chat' },
        },
      })
    )
  })

  it('changing the approval mode PATCHes the mode and keeps the channel enabled', async () => {
    seedAuth(PERMS)
    let patchBody: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes(),
      'PATCH /api/v1/w/w1/ai/agents/a1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: makeAgent() }
      },
    })
    const agent = makeAgent({
      settings: { ...BASE_SETTINGS, mcp: { enabled: true, approval_mode: 'ask_in_chat' } },
    })
    renderApp(<McpChannelCard agent={agent} />)

    await userEvent.selectOptions(
      screen.getByLabelText('Approval mode for write tools'),
      'ask_in_stept'
    )

    await waitFor(() =>
      expect(patchBody).toEqual({
        settings: {
          ...BASE_SETTINGS,
          mcp: { enabled: true, approval_mode: 'ask_in_stept' },
        },
      })
    )
  })

  it('shows mode-specific helper copy', () => {
    seedAuth(PERMS)
    mockFetch({ ...baseRoutes() })
    const agent = makeAgent({
      settings: { ...BASE_SETTINGS, mcp: { enabled: true, approval_mode: 'deny' } },
    })
    renderApp(<McpChannelCard agent={agent} />)

    expect(screen.getByText('Every write tool call is rejected. Reads still work.')).toBeInTheDocument()
  })

  it('previews the exposed tools in read/write columns', async () => {
    seedAuth(PERMS)
    mockFetch({ ...baseRoutes() })
    const agent = makeAgent({
      settings: { ...BASE_SETTINGS, mcp: { enabled: true, approval_mode: 'ask_in_chat' } },
    })
    renderApp(<McpChannelCard agent={agent} />)

    expect(screen.getByText('ask_agent')).toBeInTheDocument()
    expect(screen.getByText('search_knowledge')).toBeInTheDocument()
    expect(await screen.findByText('action_get_order_status')).toBeInTheDocument()
    expect(screen.getByText('action_create_ticket')).toBeInTheDocument()
    // Not exposed over MCP: non-MCP builtins and disabled tools.
    expect(screen.queryByText('close_conversation')).not.toBeInTheDocument()
    expect(screen.queryByText('action_delete_user')).not.toBeInTheDocument()
  })

  it('one-click creates an agent-bound key and fills the raw key into the snippets', async () => {
    seedAuth(PERMS)
    let postBody: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes(),
      'POST /api/v1/w/w1/api-keys': (init) => {
        postBody = JSON.parse(init!.body as string)
        return {
          status: 201,
          body: {
            id: 'k9',
            name: postBody!.name,
            prefix: 'sk_ab12',
            scopes: postBody!.scopes,
            agent_id: 'a1',
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
            key: 'sk_stept_agentraw',
          },
        }
      },
    })
    const agent = makeAgent({
      settings: { ...BASE_SETTINGS, mcp: { enabled: true, approval_mode: 'ask_in_chat' } },
    })
    renderApp(<McpChannelCard agent={agent} />)

    await userEvent.click(screen.getByRole('button', { name: /create key for this client/i }))

    await waitFor(() =>
      expect(postBody).toEqual({
        name: 'Claude Code · Sage',
        scopes: ['read', 'write'],
        agent_id: 'a1',
      })
    )
    expect(
      await screen.findByText(
        'claude mcp add --transport http stept http://localhost:8600/mcp/agents/a1 --header "Authorization: Bearer sk_stept_agentraw"'
      )
    ).toBeInTheDocument()
    expect(screen.getByText(/shown once — copy it now/)).toBeInTheDocument()
  })

  it('lists only keys bound to this agent and revokes them', async () => {
    seedAuth(PERMS)
    const fetchFn = mockFetch({
      ...baseRoutes(),
      'GET /api/v1/w/w1/api-keys': () => ({
        body: [
          {
            id: 'k1',
            name: 'Claude Code · Sage',
            prefix: 'sk_1111',
            scopes: ['read', 'write'],
            agent_id: 'a1',
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
          },
          {
            id: 'k2',
            name: 'Zapier',
            prefix: 'sk_2222',
            scopes: ['read'],
            agent_id: null,
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
          },
          {
            id: 'k3',
            name: 'Other agent key',
            prefix: 'sk_3333',
            scopes: ['read'],
            agent_id: 'a2',
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
          },
        ],
      }),
      'DELETE /api/v1/w/w1/api-keys/k1': () => ({ body: { id: 'k1' } }),
    })
    const agent = makeAgent({
      settings: { ...BASE_SETTINGS, mcp: { enabled: true, approval_mode: 'ask_in_chat' } },
    })
    renderApp(<McpChannelCard agent={agent} />)

    expect(await screen.findByText('Claude Code · Sage')).toBeInTheDocument()
    expect(screen.queryByText('Zapier')).not.toBeInTheDocument()
    expect(screen.queryByText('Other agent key')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Revoke Claude Code · Sage' }))
    await waitFor(() =>
      expect(
        fetchFn.mock.calls.some(
          ([url, init]) => init?.method === 'DELETE' && String(url).endsWith('/api-keys/k1')
        )
      ).toBe(true)
    )
  })
})
