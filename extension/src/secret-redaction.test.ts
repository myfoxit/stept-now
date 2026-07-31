import { describe, expect, it } from 'vitest';
import {
  isSensitiveFieldAttrs,
  looksLikeSecret,
  redactFieldValue,
  SECRET_MASK,
} from './secret-redaction';

/** Ported from the old repo's `secret-redaction.test.ts` — this matrix is the
 * contract that a credential never enters a recording. */

describe('looksLikeSecret (value level)', () => {
  it('recognises common credential shapes', () => {
    expect(looksLikeSecret('sk-proj-AbCdEf123456789AbCdEf09')).toBe(true);
    expect(looksLikeSecret('sk-AbCdEf12345678')).toBe(true);
    expect(looksLikeSecret('ghp_0123456789abcdefABCDEF0123456789abcd')).toBe(true);
    expect(looksLikeSecret('AKIAIOSFODNN7EXAMPLE')).toBe(true);
    expect(looksLikeSecret('Bearer abcdef0123456789abcdef')).toBe(true);
    expect(looksLikeSecret('pk_live_0123456789abcdef')).toBe(true);
    expect(looksLikeSecret('xoxb-1234567890-abcdefghij')).toBe(true);
    expect(looksLikeSecret('ya29.a0AfH6SMBabcdefghijklmnopqrstuvwx')).toBe(true);
    expect(
      looksLikeSecret('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'),
    ).toBe(true);
  });

  it('flags long single-token high-entropy blobs with no known prefix', () => {
    expect(looksLikeSecret('xKj9$mQ2#vR7!pL4@nW8zT1cB6dF3gH5jK0')).toBe(true);
  });

  it('does not flag ordinary prose, emails or short values', () => {
    expect(looksLikeSecret('San Francisco')).toBe(false);
    expect(looksLikeSecret('hello world this is normal prose')).toBe(false);
    expect(looksLikeSecret('user@example.com')).toBe(false);
    expect(looksLikeSecret('')).toBe(false);
    expect(looksLikeSecret('short')).toBe(false);
  });
});

describe('isSensitiveFieldAttrs (field level)', () => {
  it('treats password / payment / named-secret fields as sensitive', () => {
    expect(isSensitiveFieldAttrs('input', { type: 'password' })).toBe(true);
    expect(isSensitiveFieldAttrs('input', { autocomplete: 'cc-number' })).toBe(true);
    expect(isSensitiveFieldAttrs('input', { autocomplete: 'one-time-code' })).toBe(true);
    expect(isSensitiveFieldAttrs('input', { name: 'api_secret' })).toBe(true);
    expect(isSensitiveFieldAttrs('input', { name: 'authToken' })).toBe(true);
    expect(isSensitiveFieldAttrs('input', { id: 'apiKey' })).toBe(true);
    expect(isSensitiveFieldAttrs('textarea', { name: 'api-key' })).toBe(true);
  });

  it('leaves ordinary fields and non-fields alone', () => {
    expect(isSensitiveFieldAttrs('input', { type: 'text', name: 'city' })).toBe(false);
    expect(isSensitiveFieldAttrs('div', { name: 'token' })).toBe(false);
    expect(isSensitiveFieldAttrs('select', { name: 'secret' })).toBe(false);
  });
});

describe('redactFieldValue', () => {
  it('masks secret values and sensitive fields, passes through the rest', () => {
    expect(redactFieldValue('sk-proj-AbCdEf123456789AbCdEf09', false)).toBe(SECRET_MASK);
    expect(redactFieldValue('plain-but-named', true)).toBe(SECRET_MASK);
    expect(redactFieldValue('San Francisco', false)).toBe('San Francisco');
  });
});
