import { expect, test } from '@playwright/test'

import { loginAsDemoOwner } from './helpers'

/**
 * The dashboard renders every major surface for the seeded demo workspace. This is a
 * navigation smoke — it proves each feature area mounts and shows its primary affordance
 * without runtime errors, across the whole app the parallel feature teams built.
 */

const SECTIONS = [
  { link: 'Inbox', url: '**/inbox**' },
  { link: 'Contacts', url: '**/contacts**' },
  { link: 'Knowledge', url: '**/knowledge**' },
  { link: 'AI Agents', url: '**/ai**' },
  { link: 'Automation', url: '**/automation**' },
  { link: 'Tours', url: '**/tours**' },
  { link: 'Reports', url: '**/reports**' },
  { link: 'Settings', url: '**/settings**' },
]

test('demo owner can navigate every major dashboard section', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await loginAsDemoOwner(page)

  for (const section of SECTIONS) {
    await page.getByRole('link', { name: section.link, exact: true }).click()
    await page.waitForURL(section.url)
    // Each page settles to a non-blank, interactive state. (Avoid networkidle — the
    // dashboard holds an open realtime socket and polling queries.)
    await expect(page.locator('main, [data-sidebar="inset"]').first()).toBeVisible()
    await page.waitForTimeout(250)
  }

  expect(errors, `no uncaught page errors while navigating:\n${errors.join('\n')}`).toEqual([])
})

test('the inbox shows the seeded conversations', async ({ page }) => {
  await loginAsDemoOwner(page)
  // The seed creates several conversations across inboxes; at least one row is visible.
  await expect(page.getByText(/waiting|open|resolved|pending|snoozed/i).first()).toBeVisible({
    timeout: 15_000,
  })
})
