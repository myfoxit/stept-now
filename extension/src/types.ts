/** Shared constants for messaging + storage between popup and content script. */

/** chrome.storage.local keys. */
export const STORAGE_KEYS = {
  token: 'stept_recorder_token',
  apiBase: 'stept_api_base',
  steps: 'stept_recording_steps',
  recording: 'stept_recording_active',
  url: 'stept_recording_url',
} as const;

/** Message `type` values exchanged via chrome.tabs / chrome.runtime. */
export const MESSAGES = {
  /** popup -> content: begin capturing clicks */
  start: 'stept:recorder:start',
  /** popup -> content: stop capturing */
  stop: 'stept:recorder:stop',
  /** popup -> content: are you there / recording? */
  ping: 'stept:recorder:ping',
  /** content -> popup: a step was captured */
  step: 'stept:recorder:step',
} as const;

/** content -> popup notification that a step was captured. */
export interface StepCapturedMessage {
  type: typeof MESSAGES.step;
  selector: string;
  textHint: string;
  url: string;
}
