import { DEFAULT_API_BASE, type StoredSession } from '../types';

/** The extension's persisted identity: `chrome.storage.local`, one key.
 *
 * NOTHING else from sign-in is ever written — no password, no access token, no
 * refresh token. The extension token is workspace-scoped, expires in 30 days,
 * and is re-validated server-side on every call, so this is the whole blast
 * radius of a compromised profile.
 */

const KEY = 'stept.session';

export async function loadSession(): Promise<StoredSession | null> {
  const bag = await chrome.storage.local.get(KEY);
  const raw = bag[KEY] as Partial<StoredSession> | undefined;
  if (!raw?.extensionToken || !raw.workspaceId) return null;
  return {
    apiBase: raw.apiBase || DEFAULT_API_BASE,
    extensionToken: raw.extensionToken,
    workspaceId: raw.workspaceId,
    workspaceName: raw.workspaceName ?? '',
    userName: raw.userName ?? '',
  };
}

export async function saveSession(session: StoredSession): Promise<void> {
  await chrome.storage.local.set({ [KEY]: session });
}

export async function clearSession(): Promise<void> {
  await chrome.storage.local.remove(KEY);
}

/** The API base survives sign-out so the login form remembers the instance. */
const BASE_KEY = 'stept.apiBase';

export async function loadApiBase(): Promise<string> {
  const bag = await chrome.storage.local.get(BASE_KEY);
  return (bag[BASE_KEY] as string) || DEFAULT_API_BASE;
}

export async function saveApiBase(apiBase: string): Promise<void> {
  await chrome.storage.local.set({ [BASE_KEY]: apiBase });
}
