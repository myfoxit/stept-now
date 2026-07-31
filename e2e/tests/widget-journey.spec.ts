import { expect, test, type APIRequestContext } from '@playwright/test'

import { BACKEND, demoApiContext, getDemoWidgetKey } from './helpers'

/**
 * The flagship cross-surface journey: a visitor chats through the embedded widget,
 * the message lands in the team inbox, and the seeded AI agent ("Sage") answers from
 * the knowledge base with a citation. The AI steps are driven deterministically by the
 * offline mock provider via a scripted tool directive, so no API keys are needed.
 */

interface WidgetSession {
  token: string
  conversationId: string
}

async function bootAndSend(
  request: APIRequestContext,
  widgetKey: string,
  message: string
): Promise<WidgetSession> {
  const boot = await request.post(`${BACKEND}/api/widget/boot`, {
    data: { widget_key: widgetKey },
  })
  expect(boot.ok(), await boot.text()).toBeTruthy()
  const token = (await boot.json()).token as string

  const created = await request.post(`${BACKEND}/api/widget/conversations`, {
    headers: { 'X-Widget-Token': token },
    data: { message },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  return { token, conversationId: (await created.json()).id as string }
}

/** Poll the visitor-facing message list until an agent reply arrives (or time out). */
async function waitForAgentReply(
  request: APIRequestContext,
  session: WidgetSession
): Promise<{ content: string; citations: unknown[] }> {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const res = await request.get(
      `${BACKEND}/api/widget/conversations/${session.conversationId}/messages`,
      { headers: { 'X-Widget-Token': session.token } }
    )
    if (res.ok()) {
      const body = await res.json()
      const items: Array<{ author_type?: string; direction?: string; content: string; meta?: { citations?: unknown[] } }> =
        body.items ?? body
      const reply = items.find(
        (m) => m.author_type === 'agent' || m.direction === 'out'
      )
      if (reply) return { content: reply.content, citations: reply.meta?.citations ?? [] }
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('timed out waiting for the AI agent reply')
}

test('widget message reaches the inbox and Sage answers with a citation', async ({ request }) => {
  const widgetKey = await getDemoWidgetKey(request)

  // Visitor asks a question that steers the mock agent to search the knowledge base.
  const session = await bootAndSend(
    request,
    widgetKey,
    'How do I install the widget? [[tool:search_knowledge {"query": "install the widget"}]]'
  )

  // The agent answers from the seeded docs, citing its source.
  const reply = await waitForAgentReply(request, session)
  expect(reply.content.length).toBeGreaterThan(0)
  expect(reply.citations.length, 'AI reply carries at least one citation').toBeGreaterThan(0)

  // The same conversation is visible to the support team in the dashboard inbox.
  const { token, workspaceId } = await demoApiContext(request)
  const convs = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/conversations`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const list = (await convs.json()).items as Array<{ id: string }>
  expect(list.some((c) => c.id === session.conversationId)).toBeTruthy()
})

test('the widget loader boots and renders its launcher on a real host page', async ({
  page,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  await page.goto(`/widget-host.html?key=${widgetKey}&api=${encodeURIComponent(BACKEND)}`)

  // The loader injects a launcher button (id="stept-launcher") + the messenger iframe.
  await expect(page.locator('#stept-launcher')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('iframe')).toHaveCount(1)
})
