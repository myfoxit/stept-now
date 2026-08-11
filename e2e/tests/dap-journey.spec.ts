import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  BACKEND,
  bearer,
  demoApiContext,
  fillTourStep,
  getDemoWidgetKey,
  liveTourIds,
  loginAsDemoOwner,
  markToursSeen,
  openWidgetHost,
} from './helpers'

/**
 * The digital-adoption surfaces, end to end across three real processes: the
 * dashboard (React on :5281), the API (:8611) and the widget loader served from
 * the API but running in the HOST page's DOM on the dashboard origin — i.e. the
 * same cross-origin embed a customer gets.
 *
 * Tours, checklists and surveys all render in the host document (only the
 * messenger lives in an iframe), so everything here is asserted against the
 * page itself.
 */

/**
 * There is one overlay slot, and the seeded banner (`url: *`, priority 10) is
 * eligible on every page. Specs that are not about the banner mark the live
 * tours "seen" first — exactly what the widget itself does after a dismissal.
 */
async function silenceSeededTours(page: Page, widgetKey: string, request: APIRequestContext) {
  await markToursSeen(page, widgetKey, await liveTourIds(request))
}

/** Open the checklist panel whatever its auto-open state (it opens by itself
 * only when no tour or survey claimed the screen first). */
async function openChecklistPanel(page: Page) {
  const pill = page.getByRole('button', { name: /Getting started/ })
  await expect(pill).toBeVisible({ timeout: 20_000 })
  if ((await pill.getAttribute('aria-expanded')) !== 'true') await pill.click()
  const panel = page.getByRole('dialog', { name: 'Getting started with Stept' })
  await expect(panel).toBeVisible()
  return panel
}

test('a tour authored in the dashboard plays on a host page and its events land in analytics', async ({
  page,
  browser,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  const { token, workspaceId } = await demoApiContext(request)
  const stamp = Date.now()
  const name = `E2E authored tour ${stamp}`
  const stepOne = `Knowledge lives here ${stamp}`
  const stepTwo = `AI answers from it ${stamp}`
  // Scoped to a hash only this spec visits, so a live leftover can never hijack
  // the other specs' host pages.
  const stageHash = '#e2e-authored-tour'

  // --- author it in the dashboard -------------------------------------------
  await loginAsDemoOwner(page)
  await page.getByRole('link', { name: 'Tours', exact: true }).click()
  await page.waitForURL('**/tours')

  await page.getByRole('button', { name: /new tour/i }).click()
  const newTourDialog = page.getByRole('dialog', { name: 'New tour' })
  await newTourDialog.getByLabel('Name').fill(name)
  await newTourDialog.getByRole('button', { name: 'Create tour' }).click()
  await page.waitForURL(/\/tours\/[0-9a-f-]+$/)
  const tourId = page.url().split('/').pop() as string

  try {
    await page.getByLabel('Trigger').selectOption('url_match')
    await page.getByLabel('URL pattern').fill('*e2e-authored-tour*')
    // Beats the seeded banner (priority 10) for the single overlay slot.
    await page.getByLabel('Priority').fill('50')

    await page.getByRole('button', { name: /add step/i }).click()
    await fillTourStep(page, 0, {
      selector: '[data-tour="knowledge"]',
      title: stepOne,
      body: 'Docs and articles your **AI agent** cites.',
    })

    await page.getByRole('button', { name: /add step/i }).click()
    await fillTourStep(page, 1, { selector: '[data-tour="ai"]', title: stepTwo })

    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Tour saved')).toBeVisible()
    await page.getByRole('button', { name: 'Publish' }).click()
    // The publish/pause toggle flipping is the durable signal it went live.
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()

    // --- play it as a visitor on the embedding site --------------------------
    const visitor = await browser.newContext()
    const host = await visitor.newPage()
    try {
      await openWidgetHost(host, widgetKey, stageHash)

      // Default autostart policy is `ask`: a pushed flow tour never hijacks the
      // page — it arrives as a compact offer pill, and playing is the
      // visitor's choice. (Banners are exempt and render directly.)
      const offer = host.locator('#stept-tour-pill')
      await expect(offer).toBeVisible({ timeout: 20_000 })
      await expect(offer).toContainText(name)
      await offer.getByRole('button', { name: 'Start' }).click()

      const firstTip = host.getByRole('dialog', { name: stepOne })
      await expect(firstTip).toBeVisible({ timeout: 20_000 })
      await expect(firstTip).toContainText('1 of 2')
      // The spotlight cut-out is anchored, i.e. the selector actually resolved.
      await expect(host.locator('.stept-tour-hole')).toBeVisible()

      await firstTip.getByRole('button', { name: 'Next' }).click()
      const secondTip = host.getByRole('dialog', { name: stepTwo })
      await expect(secondTip).toBeVisible()
      await expect(secondTip).toContainText('2 of 2')

      const completedReported = host.waitForResponse(
        (res) =>
          res.url().includes(`/api/widget/tours/${tourId}/events`) &&
          res.request().method() === 'POST' &&
          (res.request().postDataJSON() as { event?: string })?.event === 'completed'
      )
      await secondTip.getByRole('button', { name: 'Done' }).click()
      await completedReported
      await expect(host.locator('.stept-tour-tip')).toHaveCount(0)
    } finally {
      await visitor.close()
    }

    // --- the dashboard sees the real telemetry -------------------------------
    await page.getByRole('link', { name: /analytics/i }).click()
    await page.waitForURL(`**/tours/${tourId}/analytics`)

    const funnel = page.getByTestId('funnel-step')
    await expect(funnel).toHaveCount(2)
    await expect(funnel.first()).toContainText('1 viewed · 0 dropped')
    await expect(funnel.nth(1)).toContainText('1 viewed · 0 dropped')
    // Completion-rate tile hint — one start, one completion, nothing dropped.
    await expect(page.getByText('1 completed')).toBeVisible()
    await expect(page.getByText('100%')).toBeVisible()
    // started + step_viewed ×2 + completed, all posted by the real player.
    await expect(page.getByTestId('event-row')).toHaveCount(4)
  } finally {
    // Never leave a live, broadly-targeted tour behind for the other specs.
    await request.delete(`${BACKEND}/api/v1/w/${workspaceId}/tours/${tourId}`, {
      headers: bearer(token),
    })
  }
})

test('the seeded checklist ticks an item and the progress survives a reload', async ({
  page,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  await silenceSeededTours(page, widgetKey, request)

  await openWidgetHost(page, widgetKey)
  const panel = await openChecklistPanel(page)
  await expect(panel).toContainText('0 of 3')

  const item = panel.getByRole('checkbox', { name: 'Invite a teammate' })
  await expect(item).toHaveAttribute('aria-checked', 'false')
  await item.click()
  await expect(item).toHaveAttribute('aria-checked', 'true')
  await expect(panel).toContainText('1 of 3')
  await expect(page.getByRole('button', { name: /Getting started/ })).toContainText('1/3')

  await page.reload()
  await expect(page.locator('#stept-launcher')).toBeVisible({ timeout: 15_000 })
  const reopened = await openChecklistPanel(page)
  await expect(reopened).toContainText('1 of 3')
  await expect(reopened.getByRole('checkbox', { name: 'Invite a teammate' })).toHaveAttribute(
    'aria-checked',
    'true'
  )
})

test('a checklist CTA starts its linked tour, and finishing the tour ticks the item', async ({
  page,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  const { token, workspaceId } = await demoApiContext(request)

  const checklists = (await (
    await request.get(`${BACKEND}/api/v1/w/${workspaceId}/checklists`, { headers: bearer(token) })
  ).json()) as Array<{ id: string; name: string }>
  const checklist = checklists.find((c) => c.name === 'Getting started with Stept')
  expect(checklist, 'the demo checklist is seeded').toBeTruthy()

  const statsUrl = `${BACKEND}/api/v1/w/${workspaceId}/checklists/${checklist!.id}/stats`
  const itemCount = async () => {
    const stats = await (await request.get(statsUrl, { headers: bearer(token) })).json()
    const item = (stats.items as Array<{ id: string; completed_count: number }>).find(
      (i) => i.id === 'take-the-welcome-tour'
    )
    return item?.completed_count ?? 0
  }
  const before = await itemCount()

  // The welcome tour only auto-delivers on inbox URLs — reaching it from the
  // checklist goes through `Stept('startTour', id)`, i.e. the by-id lookup.
  await silenceSeededTours(page, widgetKey, request)
  await openWidgetHost(page, widgetKey)
  const panel = await openChecklistPanel(page)
  await panel.getByRole('button', { name: 'Start' }).first().click()

  const tip = page.locator('.stept-tour-tip')
  await expect(tip).toBeVisible({ timeout: 20_000 })
  await expect(tip).toContainText('Your shared inbox')
  await tip.getByRole('button', { name: 'Next' }).click()
  await expect(tip).toContainText('Build your knowledge base')
  await tip.getByRole('button', { name: 'Next' }).click()
  await expect(tip).toContainText('Let AI help')

  const completedReported = page.waitForResponse(
    (res) =>
      res.url().includes('/events') &&
      res.request().method() === 'POST' &&
      (res.request().postDataJSON() as { event?: string })?.event === 'completed'
  )
  await tip.getByRole('button', { name: 'Done' }).click()
  await completedReported

  // The item whose completion is "tour_completed" ticks itself, in the widget…
  await expect(page.getByRole('button', { name: /Getting started/ })).toContainText('1/3')
  const reopened = await openChecklistPanel(page)
  await expect(
    reopened.getByRole('checkbox', { name: 'Take the welcome tour' })
  ).toHaveAttribute('aria-checked', 'true')
  // …and authoritatively on the server, for the identified visitor.
  expect(await itemCount()).toBe(before + 1)
})

test('the seeded NPS survey collects an answer that shows up in the results page', async ({
  page,
  request,
}) => {
  const { token, workspaceId } = await demoApiContext(request)
  const widgetKey = await getDemoWidgetKey(request)
  const surveysRes = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/surveys`, {
    headers: bearer(token),
  })
  const survey = ((await surveysRes.json()) as Array<{ id: string; name: string }>).find(
    (s) => s.name === 'How are we doing?'
  )
  expect(survey, 'the demo NPS survey is seeded').toBeTruthy()

  const before = await (
    await request.get(`${BACKEND}/api/v1/w/${workspaceId}/surveys/${survey!.id}/results`, {
      headers: bearer(token),
    })
  ).json()

  const feedback = `Airship docking guidance is superb ${Date.now()}`
  await silenceSeededTours(page, widgetKey, request)
  // The seeded survey targets `*/inbox*`; the hash puts the host page on one.
  await openWidgetHost(page, widgetKey, '#/inbox')

  // The card re-points `aria-labelledby` at the current question, so its
  // accessible name changes as the flow advances — anchor on the widget's own
  // class hook instead.
  const card = page.locator('.stept-sv-card')
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).toContainText('How likely are you to recommend Stept')

  await card.getByRole('button', { name: '9', exact: true }).click()
  await expect(card).toContainText('What is the main reason for your score?')
  await card.getByRole('textbox').fill(feedback)

  const stored = page.waitForResponse(
    (res) =>
      res.url().includes(`/api/widget/surveys/${survey!.id}/responses`) &&
      res.request().method() === 'POST' &&
      res.ok()
  )
  await card.getByRole('button', { name: 'Submit' }).click()
  await stored
  await expect(card).toContainText('Thanks for the feedback!')

  // The team sees it on the results page.
  await loginAsDemoOwner(page)
  await page.getByRole('link', { name: 'Surveys', exact: true }).click()
  await page.getByRole('link', { name: 'Results for How are we doing?' }).click()
  await page.waitForURL(`**/surveys/${survey!.id}/results`)

  await expect(
    page.getByText(`${before.completed + 1} of ${before.responses + 1} finished`)
  ).toBeVisible()
  await expect(page.getByTestId('text-answer').filter({ hasText: feedback })).toHaveCount(1)
})

test('a preview link plays an unpublished tour that a normal visit never sees', async ({
  page,
  browser,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  const { token, workspaceId } = await demoApiContext(request)
  const stamp = Date.now()
  const name = `E2E preview draft ${stamp}`
  const stepTitle = `Draft-only step ${stamp}`
  const stageHash = '#e2e-preview-stage'

  await loginAsDemoOwner(page)
  await page.getByRole('link', { name: 'Tours', exact: true }).click()
  await page.getByRole('button', { name: /new tour/i }).click()
  const newTourDialog = page.getByRole('dialog', { name: 'New tour' })
  await newTourDialog.getByLabel('Name').fill(name)
  await newTourDialog.getByRole('button', { name: 'Create tour' }).click()
  await page.waitForURL(/\/tours\/[0-9a-f-]+$/)
  const tourId = page.url().split('/').pop() as string

  try {
    // Targeting that WOULD win the page if this tour were live — so "it did not
    // play" can only be explained by it still being a draft.
    await page.getByLabel('Trigger').selectOption('url_match')
    await page.getByLabel('URL pattern').fill('*e2e-preview-stage*')
    await page.getByLabel('Priority').fill('50')
    await page.getByRole('button', { name: /add step/i }).click()
    await fillTourStep(page, 0, { selector: '[data-tour="knowledge"]', title: stepTitle })
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Tour saved')).toBeVisible()
    // Never published: the header still offers Publish, and the badge says draft.
    await expect(page.getByRole('button', { name: 'Publish' })).toBeVisible()
    await expect(page.getByText('draft', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: /copy preview link/i }).click()
    const snippet = await page.getByTestId('preview-link').innerText()
    const previewHash = snippet.slice(snippet.indexOf('#'))
    expect(previewHash).toContain('#stept-preview=')

    const author = await browser.newContext()
    const authorPage = await author.newPage()
    try {
      await openWidgetHost(authorPage, widgetKey, previewHash)
      const tip = authorPage.locator('.stept-tour-tip')
      await expect(tip).toBeVisible({ timeout: 20_000 })
      await expect(tip).toContainText(stepTitle)
      await expect(tip.locator('.stept-tour-badge')).toHaveText('Preview')
      await tip.getByRole('button', { name: 'Dismiss tour' }).click()
      await expect(tip).toBeHidden()
    } finally {
      await author.close()
    }

    // A visitor on the very same URL, without the token, gets the live banner
    // instead — the draft stays invisible.
    const visitor = await browser.newContext()
    const visitorPage = await visitor.newPage()
    try {
      await openWidgetHost(visitorPage, widgetKey, stageHash)
      await expect(visitorPage.locator('.stept-tour-banner')).toBeVisible({ timeout: 20_000 })
      await expect(visitorPage.getByText(stepTitle)).toHaveCount(0)
    } finally {
      await visitor.close()
    }
  } finally {
    await request.delete(`${BACKEND}/api/v1/w/${workspaceId}/tours/${tourId}`, {
      headers: bearer(token),
    })
  }
})

test('the seeded banner renders as a bar and never blocks the page underneath', async ({
  page,
  request,
}) => {
  const widgetKey = await getDemoWidgetKey(request)
  await openWidgetHost(page, widgetKey)

  // A control the embedding site owns, well clear of the top bar.
  await page.evaluate(() => {
    const cta = document.createElement('button')
    cta.id = 'host-cta'
    cta.textContent = 'Buy the airship'
    cta.style.cssText = 'position:fixed;left:24px;bottom:24px;padding:12px 18px'
    cta.addEventListener('click', () => cta.setAttribute('data-clicked', 'yes'))
    document.body.appendChild(cta)
  })

  const banner = page.locator('.stept-tour-banner')
  await expect(banner).toBeVisible({ timeout: 20_000 })
  await expect(banner).toContainText('New: AI answers with citations')
  // A banner is an announcement, never a modal: no full-screen veil, and the
  // overlay root stays click-through.
  await expect(page.locator('.stept-tour-root')).not.toHaveClass(/stept-veil/)
  await expect(page.locator('.stept-tour-tip')).toBeHidden()

  // Playwright's actionability check does real hit-testing: this click fails if
  // anything the widget mounted intercepts pointer events.
  await page.getByRole('button', { name: 'Buy the airship' }).click()
  await expect(page.locator('#host-cta')).toHaveAttribute('data-clicked', 'yes')

  await banner.getByRole('button', { name: 'Got it' }).click()
  await expect(banner).toBeHidden()
})
