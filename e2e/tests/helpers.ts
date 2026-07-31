import { expect, type APIRequestContext, type Page } from '@playwright/test'

/** The e2e backend (see playwright.config.ts webServer — port 8611). */
export const BACKEND = 'http://localhost:8611'

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

interface DemoContext {
  token: string
  workspaceId: string
}

/** Authenticate against the API as the demo owner; returns bearer token + workspace id. */
export async function demoApiContext(request: APIRequestContext): Promise<DemoContext> {
  const login = await request.post(`${BACKEND}/api/v1/auth/login`, {
    data: { email: 'owner@stept.dev', password: 'stept-demo' },
  })
  expect(login.ok(), await login.text()).toBeTruthy()
  const token = (await login.json()).access_token as string
  const me = await request.get(`${BACKEND}/api/v1/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const workspaceId = (await me.json()).memberships[0].workspace.id as string
  return { token, workspaceId }
}

/** Fetch the seeded demo workspace's widget-inbox embed key (wk_…). */
export async function getDemoWidgetKey(request: APIRequestContext): Promise<string> {
  const { token, workspaceId } = await demoApiContext(request)
  const inboxes = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/inboxes`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const list = await inboxes.json()
  const widget = (list.items ?? list).find(
    (i: { channel_type: string; widget_key?: string }) => i.channel_type === 'widget'
  )
  expect(widget?.widget_key, 'seeded widget inbox has an embed key').toBeTruthy()
  return widget.widget_key as string
}
