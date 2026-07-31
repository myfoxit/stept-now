/**
 * Pure builders for the recorder save flow. No chrome.* / network usage, so
 * these are unit-tested directly.
 *
 * The request body must match the backend exactly (see
 * backend/app/api/widget/tours.py → POST /api/widget/tours/recorder and
 * schemas.tours.RecorderTourIn):
 *   { token, name, url_pattern?, steps: [{ selector, title?, body? }] }
 */

/** A step as held in the popup / storage (title & body are always present, even
 *  if empty, so the UI has controlled inputs). */
export interface RecordedStep {
  selector: string;
  title: string;
  body: string;
  /** Original text captured from the element; used to pre-fill the title. */
  textHint?: string;
}

/** A single step in the outgoing request body. */
export interface RecorderStepPayload {
  selector: string;
  title?: string;
  body?: string;
}

/** The outgoing request body for POST /api/widget/tours/recorder. */
export interface RecorderTourPayload {
  token: string;
  name: string;
  url_pattern?: string;
  steps: RecorderStepPayload[];
}

export interface BuildPayloadInput {
  token: string;
  name: string;
  urlPattern?: string;
  steps: RecordedStep[];
}

/**
 * Build the exact request body. Empty `title`/`body` are omitted so the backend
 * fills its default "Step N" titles; an empty `url_pattern` is omitted so the
 * tour stays manually-triggered.
 */
export function buildRecorderPayload(input: BuildPayloadInput): RecorderTourPayload {
  const payload: RecorderTourPayload = {
    token: input.token.trim(),
    name: input.name.trim(),
    steps: input.steps.map((step) => {
      const out: RecorderStepPayload = { selector: step.selector };
      const title = step.title?.trim();
      const body = step.body?.trim();
      if (title) out.title = title;
      if (body) out.body = body;
      return out;
    }),
  };

  const urlPattern = input.urlPattern?.trim();
  if (urlPattern) payload.url_pattern = urlPattern;

  return payload;
}

/**
 * Suggest a `url_pattern` (fnmatch-style glob, `*` wildcard — matched by the
 * backend) from the page the tour was recorded on. Returns an empty string for
 * an unparseable URL.
 */
export function suggestUrlPattern(href: string): string {
  try {
    const url = new URL(href);
    return `${url.origin}${url.pathname}*`;
  } catch {
    return '';
  }
}
