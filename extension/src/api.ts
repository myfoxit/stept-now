/** The single network call: save the recorded tour to Stept.
 *
 * POST {apiBase}/api/widget/tours/recorder
 *   body:  { token, name, url_pattern?, steps: [{ selector, title?, body? }] }
 *   201 -> { id, name, app_url }
 *   401 -> bad / expired recorder token
 * The /api/widget/* endpoints send wildcard CORS, so no host_permissions are
 * needed for this cross-origin request from the extension popup.
 */

import { buildRecorderPayload, type RecordedStep } from './payload';

export interface SaveSuccess {
  ok: true;
  id: string;
  name: string;
  appUrl: string;
}

export interface SaveFailure {
  ok: false;
  status: number;
  message: string;
}

export type SaveResult = SaveSuccess | SaveFailure;

export interface SaveTourParams {
  apiBase: string;
  token: string;
  name: string;
  urlPattern?: string;
  steps: RecordedStep[];
}

export async function saveTour(params: SaveTourParams): Promise<SaveResult> {
  const base = params.apiBase.trim().replace(/\/+$/, '');
  const payload = buildRecorderPayload({
    token: params.token,
    name: params.name,
    urlPattern: params.urlPattern,
    steps: params.steps,
  });

  let res: Response;
  try {
    res = await fetch(`${base}/api/widget/tours/recorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    return {
      ok: false,
      status: 0,
      message: `Cannot reach ${base || 'the API'}. Is the backend running and the API base correct?`,
    };
  }

  if (res.ok) {
    const data = (await res.json()) as { id: string; name: string; app_url: string };
    return { ok: true, id: data.id, name: data.name, appUrl: data.app_url };
  }

  if (res.status === 401) {
    return {
      ok: false,
      status: 401,
      message:
        'Recorder token is invalid or expired. In Stept open Tours → Connect recorder to get a fresh token.',
    };
  }

  // Stept error envelope: { error: { code, message, details? } }.
  let message = `Save failed (HTTP ${res.status}).`;
  try {
    const body = (await res.json()) as {
      error?: { message?: string };
      detail?: unknown;
    };
    if (body?.error?.message) message = body.error.message;
    else if (typeof body?.detail === 'string') message = body.detail;
  } catch {
    /* non-JSON body — keep the generic message */
  }
  return { ok: false, status: res.status, message };
}
