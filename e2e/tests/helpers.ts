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

/** `Authorization` header for a demo bearer token. */
export function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` }
}

// --- the embedded widget on a real host page --------------------------------

/**
 * URL of `frontend/public/widget-host.html` — a plain page on a DIFFERENT
 * origin than the API that loads the real `loader.js` from the backend. It
 * carries the `[data-tour="inbox"|"knowledge"|"ai"]` anchors the seeded and
 * authored tours target.
 *
 * `hash` is appended verbatim: `#/inbox` makes the page URL match the seeded
 * survey's inbox URL glob without needing a second host page.
 */
export function widgetHostUrl(widgetKey: string, hash = ''): string {
  return `/widget-host.html?key=${widgetKey}&api=${encodeURIComponent(BACKEND)}${hash}`
}

/** Open the host page and wait for the loader to have mounted its launcher. */
export async function openWidgetHost(page: Page, widgetKey: string, hash = ''): Promise<void> {
  await page.goto(widgetHostUrl(widgetKey, hash))
  await expect(page.locator('#stept-launcher')).toBeVisible({ timeout: 15_000 })
}

/** Every live tour in the demo workspace, newest API shape. */
export async function liveTourIds(request: APIRequestContext): Promise<string[]> {
  const { token, workspaceId } = await demoApiContext(request)
  const res = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/tours`, {
    headers: bearer(token),
  })
  expect(res.ok(), await res.text()).toBeTruthy()
  const tours = (await res.json()) as Array<{ id: string; status: string }>
  return tours.filter((t) => t.status === 'live').map((t) => t.id)
}

/**
 * Pre-fill the widget's local "already seen" tour set before the loader boots.
 *
 * The seeded banner ("What's new in Stept", priority 10, url `*`) is eligible on
 * every page, so a spec about surveys or checklists would otherwise race it for
 * the single overlay slot. Marking tours seen is exactly what the widget does
 * after a visitor dismisses one — no product code is bypassed.
 */
export async function markToursSeen(
  page: Page,
  widgetKey: string,
  tourIds: string[]
): Promise<void> {
  await page.addInitScript(
    ({ key, ids }: { key: string; ids: string[] }) => {
      try {
        window.localStorage.setItem(`stept:tours-seen:${key}`, JSON.stringify(ids))
      } catch {
        /* private mode — the spec will simply see the seeded banner */
      }
    },
    { key: widgetKey, ids: tourIds }
  )
}

/**
 * Fill one step of the tour editor, opening its "Advanced" disclosure first.
 *
 * The CSS-selector field lives behind that disclosure (`StepEditor`), so a spec
 * that fills `#selector-N` directly waits forever on a hidden input.
 */
export async function fillTourStep(
  page: Page,
  index: number,
  { selector, title, body }: { selector: string; title: string; body?: string }
): Promise<void> {
  await page.locator('#title-' + index).fill(title)
  if (body !== undefined) await page.locator('#body-' + index).fill(body)
  const advanced = page.getByRole('button', { name: /^Advanced/ }).nth(index)
  if ((await advanced.getAttribute('data-state')) !== 'open') await advanced.click()
  await page.locator('#selector-' + index).fill(selector)
}
