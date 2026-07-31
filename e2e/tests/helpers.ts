import { expect, type Page } from '@playwright/test'

let counter = 0

export function uniqueEmail(prefix = 'user'): string {
  counter += 1
  // Note: .test/.localhost TLDs are rejected by the backend's email validation.
  return `${prefix}-${Date.now()}-${counter}@e2e.example.com`
}

/** Sign up a fresh user and create a workspace; ends on /inbox. */
export async function signupWithWorkspace(
  page: Page,
  { name = 'E2E User', workspace = 'E2E Workspace' } = {}
): Promise<{ email: string }> {
  const email = uniqueEmail()
  await page.goto('/signup')
  await page.getByLabel('Name').fill(name)
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('password-123')
  await page.getByRole('button', { name: /create account/i }).click()
  await page.waitForURL('**/onboarding')
  await page.getByLabel('Workspace name').fill(workspace)
  await page.getByRole('button', { name: /create workspace/i }).click()
  await page.waitForURL('**/inbox**')
  return { email }
}

/** Log in as the seeded demo owner (owner@stept.dev / stept-demo). */
export async function loginAsDemoOwner(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Email').fill('owner@stept.dev')
  await page.getByLabel('Password').fill('stept-demo')
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL('**/inbox**')
  await expect(page.getByText('Stept Demo')).toBeVisible()
}
