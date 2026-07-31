import { expect, test, type APIRequestContext } from '@playwright/test'

import { BACKEND, demoApiContext, getDemoWidgetKey } from './helpers'

/**
 * The human-in-the-loop approval gate, end to end against the running server: the AI
 * agent attempts a tool that is configured to require approval (close_conversation),
 * the run durably pauses, a teammate approves it from the dashboard side, and the run
 * resumes and completes. Driven by the offline mock provider's tool directive.
 */

async function bootAndSend(request: APIRequestContext, widgetKey: string, message: string) {
  const boot = await request.post(`${BACKEND}/api/widget/boot`, { data: { widget_key: widgetKey } })
  const token = (await boot.json()).token as string
  const created = await request.post(`${BACKEND}/api/widget/conversations`, {
    headers: { 'X-Widget-Token': token },
    data: { message },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  return (await created.json()).id as string
}

async function poll<T>(fn: () => Promise<T | null>, timeoutMs = 20_000): Promise<T> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await fn()
    if (value) return value
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('poll timed out')
}

test('an approval-gated agent tool pauses the run until a human approves', async ({ request }) => {
  const widgetKey = await getDemoWidgetKey(request)
  const { token, workspaceId } = await demoApiContext(request)
  const auth = { Authorization: `Bearer ${token}` }
  const base = `${BACKEND}/api/v1/w/${workspaceId}`

  // Visitor message steers Sage to attempt closing the conversation — a require_approval tool.
  const conversationId = await bootAndSend(
    request,
    widgetKey,
    'You can close this now, thanks! [[tool:close_conversation {}]]'
  )

  // The run pauses and surfaces a pending approval for this conversation.
  const approval = await poll(async () => {
    const res = await request.get(`${base}/ai/approvals?status=pending`, { headers: auth })
    if (!res.ok()) return null
    const items = ((await res.json()).items ?? (await res.json())) as Array<{
      id: string
      conversation_id: string
      tool_key: string
    }>
    return items.find((a) => a.conversation_id === conversationId) ?? null
  })
  expect(approval.tool_key).toContain('close')

  // A teammate approves it.
  const decide = await request.post(`${base}/ai/approvals/${approval.id}/decide`, {
    headers: auth,
    data: { approved: true, note: 'Looks resolved, go ahead.' },
  })
  expect(decide.ok(), await decide.text()).toBeTruthy()

  // The resumed run executes the deferred tool and the conversation ends up resolved.
  await poll(async () => {
    const res = await request.get(`${base}/conversations/${conversationId}`, { headers: auth })
    if (!res.ok()) return null
    const status = (await res.json()).status as string
    return status === 'resolved' ? status : null
  })
})
