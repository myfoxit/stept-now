/**
 * Minimal ambient typings for the subset of the Chrome extension (MV3) API this
 * extension consumes. `@types/chrome` is not available in the workspace store,
 * so we declare only what we use (promise-based MV3 signatures). Not exhaustive
 * by design.
 */

interface ChromeStorageChange {
  oldValue?: unknown;
  newValue?: unknown;
}

interface ChromeStorageArea {
  get(
    keys: string | string[] | Record<string, unknown> | null,
  ): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

interface ChromeStorageOnChanged {
  addListener(
    cb: (changes: Record<string, ChromeStorageChange>, areaName: string) => void,
  ): void;
  removeListener(
    cb: (changes: Record<string, ChromeStorageChange>, areaName: string) => void,
  ): void;
}

interface ChromeMessageEvent {
  addListener(
    cb: (
      message: unknown,
      sender: unknown,
      sendResponse: (response?: unknown) => void,
    ) => void | boolean,
  ): void;
  removeListener(
    cb: (
      message: unknown,
      sender: unknown,
      sendResponse: (response?: unknown) => void,
    ) => void | boolean,
  ): void;
}

interface ChromeTab {
  id?: number;
  url?: string;
  active: boolean;
}

interface ChromeScriptingInjection {
  target: { tabId: number; allFrames?: boolean };
  files?: string[];
  func?: (...args: unknown[]) => unknown;
}

declare const chrome: {
  storage: {
    local: ChromeStorageArea;
    onChanged: ChromeStorageOnChanged;
  };
  runtime: {
    id?: string;
    lastError?: { message?: string };
    sendMessage(message: unknown): Promise<unknown>;
    onMessage: ChromeMessageEvent;
  };
  tabs: {
    query(queryInfo: {
      active?: boolean;
      currentWindow?: boolean;
    }): Promise<ChromeTab[]>;
    sendMessage(tabId: number, message: unknown): Promise<unknown>;
  };
  scripting: {
    executeScript(injection: ChromeScriptingInjection): Promise<unknown[]>;
  };
};
