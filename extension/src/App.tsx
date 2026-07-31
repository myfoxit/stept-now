import { useCallback, useEffect, useState } from 'preact/hooks';

import { saveTour, type SaveResult } from './api';
import { suggestUrlPattern, type RecordedStep } from './payload';
import {
  DEFAULT_API_BASE,
  loadRecording,
  loadSettings,
  loadSteps,
  saveSettings,
  saveSteps,
  setRecording,
} from './storage';
import { MESSAGES, STORAGE_KEYS } from './types';

interface ActiveTab {
  id: number;
  url: string;
}

async function getActiveTab(): Promise<ActiveTab | null> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab || tab.id === undefined) return null;
  return { id: tab.id, url: tab.url ?? '' };
}

async function ensureInjected(tabId: number): Promise<void> {
  // Idempotent: the content script guards against duplicate installation.
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
}

const RESTRICTED_PAGE_NOTE =
  'This page can’t be recorded (e.g. chrome://, the Web Store, or a PDF). Open your app’s page and click Start again.';

export function App() {
  const [token, setToken] = useState('');
  const [apiBase, setApiBase] = useState(DEFAULT_API_BASE);
  const [steps, setSteps] = useState<RecordedStep[]>([]);
  const [recording, setRecordingState] = useState(false);
  const [name, setName] = useState('');
  const [urlPattern, setUrlPattern] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<SaveResult | null>(null);
  const [notice, setNotice] = useState('');

  const refreshSteps = useCallback(async () => {
    setSteps(await loadSteps());
  }, []);

  const persistSteps = useCallback(async (next: RecordedStep[]) => {
    setSteps(next);
    await saveSteps(next);
  }, []);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const [settings, loadedSteps, rec] = await Promise.all([
        loadSettings(),
        loadSteps(),
        loadRecording(),
      ]);
      if (cancelled) return;
      setToken(settings.token);
      setApiBase(settings.apiBase);
      setSteps(loadedSteps);
      setRecordingState(rec.active);

      const tab = await getActiveTab();
      const source = rec.url || tab?.url || '';
      if (source) setUrlPattern((prev) => prev || suggestUrlPattern(source));

      // Re-arm the current tab if a recording is already in progress (handles
      // the case where the user navigated to a new page mid-recording).
      if (rec.active && tab) {
        try {
          await ensureInjected(tab.id);
          await chrome.tabs.sendMessage(tab.id, { type: MESSAGES.start });
        } catch {
          if (!cancelled) setNotice(RESTRICTED_PAGE_NOTE);
        }
      }
    })();

    const onChanged = (
      changes: Record<string, { newValue?: unknown }>,
      area: string,
    ): void => {
      if (area !== 'local') return;
      if (changes[STORAGE_KEYS.steps]) {
        setSteps((changes[STORAGE_KEYS.steps].newValue as RecordedStep[]) ?? []);
      }
      if (changes[STORAGE_KEYS.recording]) {
        setRecordingState(Boolean(changes[STORAGE_KEYS.recording].newValue));
      }
    };
    chrome.storage.onChanged.addListener(onChanged);

    const onMessage = (message: unknown): void => {
      if ((message as { type?: string } | null)?.type === MESSAGES.step) {
        void refreshSteps();
      }
    };
    chrome.runtime.onMessage.addListener(onMessage);

    return () => {
      cancelled = true;
      chrome.storage.onChanged.removeListener(onChanged);
      chrome.runtime.onMessage.removeListener(onMessage);
    };
  }, [refreshSteps]);

  const onTokenInput = (value: string) => {
    setToken(value);
    void saveSettings({ token: value });
  };
  const onApiBaseInput = (value: string) => {
    setApiBase(value);
    void saveSettings({ apiBase: value });
  };

  const handleStart = async () => {
    setResult(null);
    setNotice('');
    const tab = await getActiveTab();
    if (!tab) {
      setNotice('No active tab to record.');
      return;
    }
    try {
      await ensureInjected(tab.id);
      await chrome.tabs.sendMessage(tab.id, { type: MESSAGES.start });
    } catch {
      setNotice(RESTRICTED_PAGE_NOTE);
      return;
    }
    await setRecording(true, tab.url);
    setRecordingState(true);
    if (!urlPattern && tab.url) setUrlPattern(suggestUrlPattern(tab.url));
  };

  const handleStop = async () => {
    const tab = await getActiveTab();
    if (tab) {
      try {
        await chrome.tabs.sendMessage(tab.id, { type: MESSAGES.stop });
      } catch {
        /* content script may be gone after navigation — ignore */
      }
    }
    await setRecording(false);
    setRecordingState(false);
  };

  const updateStep = (index: number, patch: Partial<RecordedStep>) => {
    void persistSteps(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };
  const deleteStep = (index: number) => {
    void persistSteps(steps.filter((_, i) => i !== index));
  };
  const moveStep = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= steps.length) return;
    const next = steps.slice();
    const tmp = next[index];
    next[index] = next[target];
    next[target] = tmp;
    void persistSteps(next);
  };
  const clearSteps = () => void persistSteps([]);

  const handleSave = async () => {
    setResult(null);
    setNotice('');
    if (!token.trim()) {
      setNotice('Paste a recorder token first.');
      return;
    }
    if (!name.trim()) {
      setNotice('Give the tour a name.');
      return;
    }
    if (steps.length === 0) {
      setNotice('Record at least one step before saving.');
      return;
    }
    setSaving(true);
    const res = await saveTour({ apiBase, token, name, urlPattern, steps });
    setSaving(false);
    setResult(res);
    if (res.ok) {
      await setRecording(false);
      setRecordingState(false);
      await saveSteps([]);
      setSteps([]);
    }
  };

  return (
    <div class="app">
      <header class="header">
        <h1>Stept Tour Recorder</h1>
        {recording ? <span class="rec-dot" title="Recording">● REC</span> : null}
      </header>

      <section class="panel">
        <label class="field">
          <span>Recorder token</span>
          <input
            type="password"
            placeholder="Paste from Stept → Tours → Connect recorder"
            value={token}
            onInput={(e) => onTokenInput((e.target as HTMLInputElement).value)}
          />
        </label>
        <label class="field">
          <span>API base</span>
          <input
            type="text"
            placeholder={DEFAULT_API_BASE}
            value={apiBase}
            onInput={(e) => onApiBaseInput((e.target as HTMLInputElement).value)}
          />
        </label>
      </section>

      <section class="controls">
        {recording ? (
          <button class="btn btn-danger" onClick={handleStop}>
            Stop recording
          </button>
        ) : (
          <button class="btn btn-primary" onClick={handleStart}>
            Start recording
          </button>
        )}
        <span class="count">
          {steps.length} step{steps.length === 1 ? '' : 's'}
        </span>
        {steps.length > 0 ? (
          <button class="btn btn-ghost" onClick={clearSteps}>
            Clear
          </button>
        ) : null}
      </section>

      {recording ? (
        <p class="hint">Click elements in the page to capture them as steps.</p>
      ) : null}

      <section class="steps">
        {steps.length === 0 ? (
          <p class="empty">No steps yet. Start recording, then click through your app.</p>
        ) : (
          <ol class="step-list">
            {steps.map((step, index) => (
              <li class="step" key={index}>
                <div class="step-top">
                  <span class="step-index">{index + 1}</span>
                  <code class="step-selector" title={step.selector}>
                    {step.selector}
                  </code>
                  <div class="step-actions">
                    <button
                      class="icon-btn"
                      title="Move up"
                      disabled={index === 0}
                      onClick={() => moveStep(index, -1)}
                    >
                      ↑
                    </button>
                    <button
                      class="icon-btn"
                      title="Move down"
                      disabled={index === steps.length - 1}
                      onClick={() => moveStep(index, 1)}
                    >
                      ↓
                    </button>
                    <button
                      class="icon-btn danger"
                      title="Delete step"
                      onClick={() => deleteStep(index)}
                    >
                      ✕
                    </button>
                  </div>
                </div>
                <input
                  class="step-title"
                  type="text"
                  placeholder={`Step ${index + 1} title`}
                  value={step.title}
                  onInput={(e) =>
                    updateStep(index, { title: (e.target as HTMLInputElement).value })
                  }
                />
                <textarea
                  class="step-body"
                  rows={2}
                  placeholder="Body (markdown, optional)"
                  value={step.body}
                  onInput={(e) =>
                    updateStep(index, {
                      body: (e.target as HTMLTextAreaElement).value,
                    })
                  }
                />
              </li>
            ))}
          </ol>
        )}
      </section>

      <section class="panel">
        <label class="field">
          <span>Tour name</span>
          <input
            type="text"
            placeholder="e.g. Getting started"
            value={name}
            onInput={(e) => setName((e.target as HTMLInputElement).value)}
          />
        </label>
        <label class="field">
          <span>URL pattern (optional)</span>
          <input
            type="text"
            placeholder="https://app.example.com/*"
            value={urlPattern}
            onInput={(e) => setUrlPattern((e.target as HTMLInputElement).value)}
          />
        </label>
      </section>

      <section class="save">
        <button
          class="btn btn-primary btn-block"
          disabled={saving || steps.length === 0}
          onClick={handleSave}
        >
          {saving ? 'Saving…' : 'Save tour'}
        </button>
      </section>

      {notice ? <p class="notice">{notice}</p> : null}

      {result && !result.ok ? <p class="notice error">{result.message}</p> : null}

      {result && result.ok ? (
        <div class="success">
          <p>
            Saved <strong>{result.name}</strong> as a draft tour.
          </p>
          <a href={result.appUrl} target="_blank" rel="noreferrer">
            Open in Stept →
          </a>
        </div>
      ) : null}

      <footer class="footer">
        Steps are captured as robust CSS selectors (data-tour → id → unique path).
      </footer>
    </div>
  );
}
