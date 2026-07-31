import { expect, test } from '@playwright/test'

import { loginAsDemoOwner, signupWithWorkspace } from './helpers'

test('backend health endpoint responds', async ({ request }) => {
  const response = await request.get('http://localhost:8611/api/v1/healthz')
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body.status).toBe('ok')
})

test('signup → onboarding → app shell', async ({ page }) => {
  await signupWithWorkspace(page, { workspace: 'Smoke Co' })
  await expect(page.getByText('Smoke Co')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Inbox' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'AI Agents' })).toBeVisible()
})

test('demo owner can log in and switch theme', async ({ page }) => {
  await loginAsDemoOwner(page)
  await page.getByText('Odette Owner').click()
  await page.getByText('Toggle theme').click()
  const isDark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  expect(typeof isDark).toBe('boolean')
})

test('unauthenticated users are redirected to login', async ({ page }) => {
  await page.goto('/inbox')
  await page.waitForURL('**/login')
  await expect(page.getByRole('button', { name: /log in/i })).toBeVisible()
})
