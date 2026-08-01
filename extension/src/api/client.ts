import type { PageSnapshot } from '@stept/dom-capture';
import type { PickedSelector, TourDetail, TourStep, TourSummary, WorkspaceChoice } from '../types';

/**
 * The extension's only network surface.
 *
 * Two auth realms:
 *  - `/api/v1/*` with the SHORT-LIVED access token, used exactly twice during
 *    sign-in (list workspaces, mint the extension token). Not wildcard-CORS —
 *    it works because the service worker has `host_permissions: <all_urls>`.
 *  - `/api/widget/dap/*` with the long-lived extension token. Open CORS;
 *    membership + `tours:manage` are re-checked server-side on EVERY call, so
 *    revoking a member instantly kills their stored token.
 *
 * The access token and the password are DISCARDED the moment the extension
 * token comes back — only `{apiBase, extensionToken, workspaceId, …}` is
 * persisted (docs/DAP2-CONTRACTS.md shared decision 4).
 *
 * Retry/single-flight patterns are ported from the old repo's `stept-api.ts`:
 * N concurrent 401s must collapse into ONE validation round-trip, and every
 * method returns a typed result rather than throwing — a network blip must
 * never break a recording in progress.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** the server rejected our identity — the caller signs out */
    readonly unauthorized = status === 401 || status === 403,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** A save/PUT rejected because the dashboard edited the tour first. */
export class ConflictError extends ApiError {
  constructor(message: string) {
    super(message, 409, false);
    this.name = 'ConflictError';
  }
}

export function normalizeBase(raw: string): string {
  return raw.trim().replace(/\/+$/, '');
}

const REQUEST_TIMEOUT_MS = 45_000;

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as {
      error?: { message?: string };
      detail?: unknown;
    };
    if (body?.error?.message) return body.error.message;
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    /* non-JSON body — fall through to the generic message */
  }
  return `Request failed (HTTP ${res.status})`;
}

async function request(url: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS), ...init });
  } catch {
    throw new ApiError(`Cannot reach ${new URL(url).origin} — is Stept running?`, 0, false);
  }
  if (res.status === 409) throw new ConflictError(await readError(res));
  if (!res.ok) throw new ApiError(await readError(res), res.status);
  return res;
}

// ---------------------------------------------------------------------------
// sign-in (access-token realm)
// ---------------------------------------------------------------------------

interface LoginResponse {
  access_token: string;
  user: { id: string; email: string; name: string };
}

interface MeResponse {
  user: { id: string; name: string; email: string };
  memberships: Array<{
    role: string;
    permissions?: string[];
    workspace: { id: string; name: string };
  }>;
}

export interface LoginOutcome {
  accessToken: string;
  userName: string;
  workspaces: WorkspaceChoice[];
}

const TOURS_MANAGE = 'tours:manage';

/** email + password → access token + the workspaces this user can record in. */
export async function login(apiBase: string, email: string, password: string): Promise<LoginOutcome> {
  const base = normalizeBase(apiBase);
  const res = await request(`${base}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const login = (await res.json()) as LoginResponse;
  const meRes = await request(`${base}/api/v1/me`, {
    headers: { authorization: `Bearer ${login.access_token}` },
  });
  const me = (await meRes.json()) as MeResponse;
  const workspaces: WorkspaceChoice[] = (me.memberships ?? []).map((m) => ({
    id: m.workspace.id,
    name: m.workspace.name,
    role: m.role,
    // owners/admins always have it; custom roles carry an explicit list
    canManageTours:
      m.permissions === undefined
        ? m.role === 'owner' || m.role === 'admin'
        : m.permissions.includes(TOURS_MANAGE) || m.permissions.includes('*'),
  }));
  return {
    accessToken: login.access_token,
    userName: me.user?.name || login.user?.name || email,
    workspaces,
  };
}

/** Exchange the access token for the long-lived, workspace-scoped extension
 * token. The access token is never persisted — this is the last thing it does. */
export async function mintExtensionToken(
  apiBase: string,
  accessToken: string,
  workspaceId: string,
): Promise<string> {
  const res = await request(
    `${normalizeBase(apiBase)}/api/v1/w/${encodeURIComponent(workspaceId)}/tours/extension-token`,
    { method: 'POST', headers: { authorization: `Bearer ${accessToken}` } },
  );
  const body = (await res.json()) as { token: string };
  return body.token;
}

// ---------------------------------------------------------------------------
// extension realm (/api/widget/dap/*)
// ---------------------------------------------------------------------------

export interface AuthCheck {
  workspace_id: string;
  workspace_name: string;
  user_name: string;
  perms_ok: boolean;
  /** Dashboard origin. Absent on backends older than the deep-link fix. */
  app_base_url?: string;
}

export class DapClient {
  /** Single-flight validation: N concurrent 401s must not fire N round-trips. */
  private checking: Promise<AuthCheck> | null = null;

  constructor(
    private readonly getBase: () => string,
    private readonly getToken: () => string | null,
  ) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const token = this.getToken();
    return { ...(token ? { authorization: `Bearer ${token}` } : {}), ...extra };
  }

  private url(path: string): string {
    return `${normalizeBase(this.getBase())}/api/widget/dap${path}`;
  }

  /** Validate the stored token. Concurrent callers share one request. */
  check(): Promise<AuthCheck> {
    if (this.checking) return this.checking;
    this.checking = (async () => {
      const res = await request(this.url('/auth/check'), {
        method: 'POST',
        headers: this.headers(),
      });
      return (await res.json()) as AuthCheck;
    })().finally(() => {
      this.checking = null;
    });
    return this.checking;
  }

  async listTours(): Promise<TourSummary[]> {
    const res = await request(this.url('/tours'), { headers: this.headers() });
    return (await res.json()) as TourSummary[];
  }

  async getTour(tourId: string): Promise<TourDetail> {
    const res = await request(this.url(`/tours/${encodeURIComponent(tourId)}`), {
      headers: this.headers(),
    });
    return (await res.json()) as TourDetail;
  }

  async createTour(name: string, steps: TourStep[], urlPattern?: string): Promise<TourDetail> {
    const res = await request(this.url('/tours'), {
      method: 'POST',
      headers: this.headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({
        name,
        ...(urlPattern ? { url_pattern: urlPattern } : {}),
        steps: steps.map(wireStep),
      }),
    });
    return (await res.json()) as TourDetail;
  }

  /** Replace the deck. 409 (ConflictError) = the dashboard changed it first. */
  async putSteps(tourId: string, steps: TourStep[], baseVersion: number): Promise<TourDetail> {
    const res = await request(this.url(`/tours/${encodeURIComponent(tourId)}/steps`), {
      method: 'PUT',
      headers: this.headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ steps: steps.map(wireStep), base_version: baseVersion }),
    });
    return (await res.json()) as TourDetail;
  }

  async patchTour(
    tourId: string,
    patch: { name?: string; url_pattern?: string | null },
  ): Promise<TourDetail> {
    const res = await request(this.url(`/tours/${encodeURIComponent(tourId)}`), {
      method: 'PATCH',
      headers: this.headers({ 'content-type': 'application/json' }),
      body: JSON.stringify(patch),
    });
    return (await res.json()) as TourDetail;
  }

  /** Upload a screenshot EAGERLY during recording (long before a draft exists)
   * and keep the returned key on the raw event. */
  async uploadScreenshot(blob: Blob, filename = 'step.jpg'): Promise<string> {
    const form = new FormData();
    form.append('file', blob, filename);
    const res = await request(this.url('/screenshots'), {
      method: 'POST',
      headers: this.headers(),
      body: form,
    });
    const body = (await res.json()) as { key: string };
    return body.key;
  }

  /** Upload one sandbox DOM replica, same eager timing as a screenshot.
   * Sent as multipart rather than a JSON body so it reuses the media pipeline
   * (and so the server can cap it by size before parsing). */
  async uploadSnapshot(snapshot: PageSnapshot): Promise<{ key: string; bytes: number }> {
    const form = new FormData();
    const blob = new Blob([JSON.stringify(snapshot)], { type: 'application/json' });
    form.append('file', blob, 'snapshot.json');
    const res = await request(this.url('/snapshots'), {
      method: 'POST',
      headers: this.headers(),
      body: form,
    });
    return (await res.json()) as { key: string; bytes: number };
  }
}

/** Strip the fields the backend does not accept and drop nulls it would reject.
 * `target` goes over verbatim — it is opaque JSON server-side. */
function wireStep(step: TourStep): Record<string, unknown> {
  const out: Record<string, unknown> = {
    id: step.id,
    type: step.type,
    selector: step.selector,
    fallback_selectors: step.fallback_selectors,
    text_hint: step.text_hint,
    title: step.title,
    body: step.body,
    placement: step.placement,
    advance: step.advance,
  };
  if (step.target) out.target = step.target;
  if (step.media) out.media = step.media;
  if (step.screenshot_key) out.screenshot_key = step.screenshot_key;
  if (step.sandbox_key) out.sandbox_key = step.sandbox_key;
  if (step.type === 'action' && step.action) out.action = step.action;
  if (step.type === 'wait' && step.wait) out.wait = step.wait;
  return out;
}

/** Public URL for a screenshot key (open CORS, no auth — the media route only
 * serves keys under `public/{workspace_id}/`). */
export function screenshotUrl(apiBase: string, workspaceId: string, key: string): string {
  return `${normalizeBase(apiBase)}/api/widget/media/${encodeURIComponent(workspaceId)}/${key}`;
}

/** Deep link into the dashboard's tour editor.
 *
 * Takes the DASHBOARD origin, not the API origin — they differ in every
 * deployment (and in dev: :5273 vs :8600). The route is `/tours/:id`; there is
 * no `/w/{workspace}` prefix in the dashboard router. */
export function tourAppUrl(appBaseUrl: string, tourId: string): string {
  return `${normalizeBase(appBaseUrl)}/tours/${encodeURIComponent(tourId)}`;
}

/** The picker's clipboard payload — kept here so the panel and the picker
 * agree on one format. */
export function formatPicked(picked: PickedSelector): string {
  const lines = [picked.selector];
  if (picked.fallbacks.length) lines.push(`fallbacks: ${picked.fallbacks.join(' | ')}`);
  if (picked.textHint) lines.push(`text: ${picked.textHint}`);
  return lines.join('\n');
}
