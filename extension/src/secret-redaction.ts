// Shared secret-redaction — ported VERBATIM from the old stept extension
// (`extension/src/secret-redaction.ts`).
//
// A reveal <input> (NOT type=password) can expose a freshly-minted API key in
// plaintext. The recorder masks by FIELD (attrs) and by VALUE (token shapes +
// entropy) so credentials never enter a recording, a screenshot upload, or a
// compiled tour. Pure string/attr logic only — no DOM, no chrome APIs.
export const SECRET_MASK = '•••• [redacted]';

// Known credential prefixes / token shapes — matches the secret VALUE (not just
// the field) so reveal inputs are covered. Anchored at the start of a token.
const SECRET_VALUE_PATTERNS: RegExp[] = [
  /\bsk-[A-Za-z0-9_-]{8,}\b/, // OpenAI (sk-, sk-proj-, sk-ant-...)
  /\b(?:pk|rk|ak)_(?:live|test)_[A-Za-z0-9]{10,}\b/, // Stripe publishable/restricted/secret keys
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/, // GitHub tokens
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/, // Slack tokens
  /\bAKIA[0-9A-Z]{16}\b/, // AWS access key id
  /\bya29\.[A-Za-z0-9_-]{20,}\b/, // Google OAuth access token
  /\bAIza[A-Za-z0-9_-]{30,}\b/, // Google API key
  /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b/, // JWT
  /\bBearer\s+[A-Za-z0-9._-]{16,}\b/i, // Authorization: Bearer ...
];

// Shannon entropy (bits/char). High-entropy long strings (no spaces) are very
// likely keys/tokens even when they carry no recognizable prefix.
function entropy(s: string): number {
  const freq: Record<string, number> = {};
  for (const ch of s) freq[ch] = (freq[ch] || 0) + 1;
  let h = 0;
  for (const k in freq) {
    const p = (freq[k] ?? 0) / s.length;
    if (p > 0) h -= p * Math.log2(p);
  }
  return h;
}

/** Does this raw value look like a credential we must not surface verbatim? */
export function looksLikeSecret(value: string): boolean {
  if (!value) return false;
  for (const re of SECRET_VALUE_PATTERNS) if (re.test(value)) return true;
  // Long, single-token, high-entropy strings (e.g. opaque API keys without a
  // documented prefix). Guard on length + no whitespace to avoid masking prose.
  const trimmed = value.trim();
  if (trimmed.length >= 24 && !/\s/.test(trimmed) && entropy(trimmed) >= 3.5) return true;
  return false;
}

// Attribute name fragments that mark a field as holding a secret even when the
// value itself carries no recognizable token shape.
const SENSITIVE_NAME_RE = /secret|token|api[-_]?key|password|passwd/i;
const SENSITIVE_AUTOCOMPLETE_RE = /cc-|one-time-code/i;

/**
 * Is this field sensitive based on its tag + attributes alone (no value match)?
 * Pass a plain attribute map ({ type, name, autocomplete, id }).
 */
export function isSensitiveFieldAttrs(tag: string, attrs: Record<string, string>): boolean {
  if (tag !== 'input' && tag !== 'textarea') return false;
  if ((attrs.type || '').toLowerCase() === 'password') return true;
  if (attrs.autocomplete && SENSITIVE_AUTOCOMPLETE_RE.test(attrs.autocomplete)) return true;
  if (attrs.name && SENSITIVE_NAME_RE.test(attrs.name)) return true;
  if (attrs.id && SENSITIVE_NAME_RE.test(attrs.id)) return true;
  return false;
}

/**
 * Mask a raw field value when it is a secret (by pattern) or already known to
 * sit in a sensitive field; otherwise pass it through unchanged.
 */
export function redactFieldValue(raw: string, sensitiveField: boolean): string {
  if (sensitiveField || looksLikeSecret(raw)) return SECRET_MASK;
  return raw;
}
