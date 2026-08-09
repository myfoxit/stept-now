import { expect, test, type Page } from '@playwright/test'

import { getDemoWidgetKey, liveTourIds, markToursSeen, openWidgetHost } from './helpers'

/**
 * The Actions SDK, end to end: the HOST page registers a function
 * (`Stept('action', …)` in widget-host.html — queued before the loader even
 * loads), the visitor asks, the agent calls it, the confirm card renders in the
 * thread, and on Run the handler executes in the host page's own session.
 *
 * Nothing is mocked but the model's tool choice (the offline provider takes
 * `[[tool:name {json}]]` directives): the def rides the visitor's message into
 * `conversation.attributes`, the engine parks the run `awaiting_client`, and
 * the result round-trips through the real widget to resume it.
 */

const MESSENGER = 'iframe#stept-frame'

async function sendFirstMessage(page: Page, text: string) {
  await page.locator('#stept-launcher').click()
  const frame = page.frameLocator(MESSENGER)
  await frame
    .getByRole('button', { name: /new conversation|send us a message/i })
    .first()
    .click()
  const composer = frame.getByRole('textbox', { name: /message/i })
  await expect(composer).toBeVisible()
  await composer.fill(text)
  await composer.press('Enter')
  return frame
}

test.beforeEach(async ({ page, request }) => {
  // One overlay slot: silence the seeded banner so nothing covers the thread.
  await markToursSeen(page, await getDemoWidgetKey(request), await liveTourIds(request))
})

test('a page-registered action runs after the visitor confirms', async ({ page, request }) => {
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  const frame = await sendFirstMessage(
    page,
    'Create a draft for 99 please [[tool:app_create_invoice_draft {"amount": "99"}]]'
  )

  // The confirm card renders in the thread with the action and its arguments.
  const card = frame.locator('.sw-action-card')
  await expect(card).toBeVisible({ timeout: 25_000 })
  await expect(card).toContainText('create invoice draft')
  await expect(card).toContainText('99')
  // Nothing has touched the page yet.
  await expect(page.locator('#action-result')).toBeHidden()

  await card.getByRole('button', { name: 'Run' }).click()

  // The handler ran in the HOST page with the visitor's session…
  await expect(page.locator('#action-result')).toContainText('Draft created for 99', {
    timeout: 20_000,
  })
  // …and the resumed run reached a reply in the thread.
  await expect(frame.locator('.sw-bubble-them').last()).toBeVisible({ timeout: 25_000 })
  await expect(card).toBeHidden()
})

test('Not now declines the action and nothing runs', async ({ page, request }) => {
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  const frame = await sendFirstMessage(
    page,
    'Create a draft [[tool:app_create_invoice_draft {"amount": "42"}]]'
  )

  const card = frame.locator('.sw-action-card')
  await expect(card).toBeVisible({ timeout: 25_000 })
  await card.getByRole('button', { name: 'Not now' }).click()

  // The run resumed with the declined result and answered in words.
  await expect(frame.locator('.sw-bubble-them').last()).toBeVisible({ timeout: 25_000 })
  await expect(card).toBeHidden()
  await page.waitForTimeout(1_000)
  await expect(page.locator('#action-result')).toBeHidden()
})
