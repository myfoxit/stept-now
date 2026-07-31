/** Thin wrapper over chrome.storage.local (kept isolated so the rest of the UI
 *  stays testable). */

import type { RecordedStep } from './payload';
import { STORAGE_KEYS } from './types';

export const DEFAULT_API_BASE = 'http://localhost:8600';

export interface RecorderSettings {
  token: string;
  apiBase: string;
}

export async function loadSettings(): Promise<RecorderSettings> {
  const data = await chrome.storage.local.get([STORAGE_KEYS.token, STORAGE_KEYS.apiBase]);
  return {
    token: (data[STORAGE_KEYS.token] as string) ?? '',
    apiBase: (data[STORAGE_KEYS.apiBase] as string) ?? DEFAULT_API_BASE,
  };
}

export async function saveSettings(patch: Partial<RecorderSettings>): Promise<void> {
  const items: Record<string, unknown> = {};
  if (patch.token !== undefined) items[STORAGE_KEYS.token] = patch.token;
  if (patch.apiBase !== undefined) items[STORAGE_KEYS.apiBase] = patch.apiBase;
  if (Object.keys(items).length) await chrome.storage.local.set(items);
}

export async function loadSteps(): Promise<RecordedStep[]> {
  const data = await chrome.storage.local.get(STORAGE_KEYS.steps);
  return (data[STORAGE_KEYS.steps] as RecordedStep[]) ?? [];
}

export async function saveSteps(steps: RecordedStep[]): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_KEYS.steps]: steps });
}

export async function loadRecording(): Promise<{ active: boolean; url: string }> {
  const data = await chrome.storage.local.get([STORAGE_KEYS.recording, STORAGE_KEYS.url]);
  return {
    active: Boolean(data[STORAGE_KEYS.recording]),
    url: (data[STORAGE_KEYS.url] as string) ?? '',
  };
}

export async function setRecording(active: boolean, url?: string): Promise<void> {
  const items: Record<string, unknown> = { [STORAGE_KEYS.recording]: active };
  if (url !== undefined) items[STORAGE_KEYS.url] = url;
  await chrome.storage.local.set(items);
}
