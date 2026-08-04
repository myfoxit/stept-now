import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  BACKEND,
  getDemoWidgetKey,
  liveTourIds,
  markToursSeen,
  openWidgetHost,
} from './helpers'

/**
 * The in-app assistant, end to end: a visitor asks in the embedded chat and the
 * AI answers *on their screen* — pointing at the real UI, and (with consent)
 * doing it for them.
 *
 * Three processes are involved and none of them is mocked: the API (:8611) runs
 * the agent loop, the widget loader runs in the HOST page's DOM on a different
 * origin, and the messenger runs in its iframe. The only scripted part is which
 * tools the model calls — the offline mock provider takes `[[tool:name {json}]]`
 * directives from the visitor's own message, so the sequence is deterministic
 * without API keys. Everything after that (deferring the call to the browser,
 * executing it in the host DOM, resuming the parked run) is the real machinery.
 *
 * The host page (`frontend/public/widget-host.html`) carries a small invoice app:
 * `[0] New invoice` reveals a form, `[1] Amount`, `[2] Save invoice` confirms.
 */

const MESSENGER = 'iframe#stept-frame'

/** Open the messenger and return a locator factory scoped to its iframe. */
async function openMessenger(page: Page) {
  await page.locator('#stept-launcher').click()
  const frame = page.frameLocator(MESSENGER)
  await expect(frame.getByRole('button', { name: /new conversation|send us a message/i }).first()).toBeVisible({
    timeout: 20_000,
  })
  return frame
}

/** Start a thread and send `text` from the visitor. */
async function sendFirstMessage(page: Page, text: string) {
  const frame = await openMessenger(page)
  await frame.getByRole('button', { name: /new conversation|send us a message/i }).first().click()
  const composer = frame.getByRole('textbox', { name: /message/i })
  await expect(composer).toBeVisible()
  await composer.fill(text)
  await composer.press('Enter')
  return frame
}

/** Make the demo agent's page-control settings explicit for this spec. */
async function setPageControl(
  request: APIRequestContext,
  { enabled, allowActions }: { enabled: boolean; allowActions: boolean }
) {
  const { demoApiContext, bearer } = await import('./helpers')
  const { token, workspaceId } = await demoApiContext(request)
  const list = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/ai/agents`, {
    headers: bearer(token),
  })
  expect(list.ok(), await list.text()).toBeTruthy()
  const agents = (await list.json()) as Array<{ id: string; name: string; settings: unknown }>
  const sage = agents.find((a) => a.name === 'Sage') ?? agents[0]
  const settings = sage.settings as Record<string, unknown>
  const patched = await request.patch(`${BACKEND}/api/v1/w/${workspaceId}/ai/agents/${sage.id}`, {
    headers: bearer(token),
    data: { settings: { ...settings, page_control: { enabled, allow_actions: allowActions } } },
  })
  expect(patched.ok(), await patched.text()).toBeTruthy()
}

test.beforeEach(async ({ page, request }) => {
  // One overlay slot: silence the seeded banner so an assistant walkthrough owns it.
  await markToursSeen(page, await getDemoWidgetKey(request), await liveTourIds(request))
})

test('the assistant walks the visitor through the real UI on their own screen', async ({
  page,
  request,
}) => {
  await setPageControl(request, { enabled: true, allowActions: false })
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  // Look at the page, then compose a walkthrough anchored to what was found.
  const frame = await sendFirstMessage(
    page,
    'How do I create an invoice? [[tool:page_snapshot {}]] ' +
      '[[tool:show_steps {"title": "Create an invoice", "steps": [{"index": 0, ' +
      '"title": "Click New invoice", "body": "This opens the invoice form."}]}]]'
  )

  // The coach-mark renders in the HOST document, anchored on the real button.
  const tip = page.locator('.stept-tour-tip')
  await expect(tip).toBeVisible({ timeout: 25_000 })
  await expect(tip).toContainText('Click New invoice')
  await expect(page.locator('.stept-tour-hole')).toBeVisible()

  // And the assistant still replies in the thread.
  await expect(frame.locator('.sw-bubble').last()).toBeVisible()
})

test('with consent the assistant fills in the form for the visitor', async ({ page, request }) => {
  await setPageControl(request, { enabled: true, allowActions: true })
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  const frame = await sendFirstMessage(page, 'Hi there')
  // The consent row only appears once the backend confirms page control is on.
  const consent = frame.getByRole('checkbox')
  await expect(consent).toBeVisible({ timeout: 25_000 })
  await consent.check()

  const composer = frame.getByRole('textbox', { name: /message/i })
  // Snapshot first: an [index] only means something once the page has been looked
  // at, which is the contract the runtime enforces (and reports when broken).
  await composer.fill(
    'Please do it for me. [[tool:page_snapshot {}]] ' +
      '[[tool:page_act {"index": 0, "kind": "click"}]] ' +
      '[[tool:page_snapshot {}]] ' +
      '[[tool:page_act {"index": 1, "kind": "fill", "text": "250"}]] ' +
      '[[tool:page_act {"index": 2, "kind": "click"}]]'
  )
  await composer.press('Enter')

  // The assistant clicked, typed and submitted in the visitor's own session.
  await expect(page.locator('#invoice-form')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('#invoice-amount')).toHaveValue('250')
  await expect(page.locator('#invoice-result')).toContainText('Invoice saved for 250', {
    timeout: 20_000,
  })
})

test('without consent the assistant cannot touch the page', async ({ page, request }) => {
  await setPageControl(request, { enabled: true, allowActions: true })
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  // Same directives, no consent given: `page_act` is never offered to the model,
  // so the call cannot be made at all — the visitor gets words, not clicks.
  const frame = await sendFirstMessage(
    page,
    'Do it for me [[tool:page_act {"index": 0, "kind": "click"}]]'
  )
  await expect(frame.locator('.sw-bubble').last()).toBeVisible({ timeout: 25_000 })
  await page.waitForTimeout(2_000)
  await expect(page.locator('#invoice-form')).toBeHidden()
  await expect(page.locator('#invoice-result')).toBeHidden()
})

test('the assistant answers a knowledge question with a citation, unchanged', async ({
  page,
  request,
}) => {
  // Page control must not have broken the plain RAG answer path.
  await setPageControl(request, { enabled: true, allowActions: false })
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  const frame = await sendFirstMessage(
    page,
    'How do I install the widget? [[tool:search_knowledge {"query": "install widget"}]]'
  )
  const reply = frame.locator('.sw-bubble-them').last()
  await expect(reply).toBeVisible({ timeout: 25_000 })
  await expect(reply).toContainText('[1]')
})
