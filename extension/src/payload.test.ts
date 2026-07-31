import { describe, expect, it } from 'vitest';

import {
  buildRecorderPayload,
  suggestUrlPattern,
  type RecordedStep,
} from './payload';

function step(overrides: Partial<RecordedStep> = {}): RecordedStep {
  return { selector: '#x', title: '', body: '', ...overrides };
}

describe('buildRecorderPayload', () => {
  it('produces the backend request shape and trims token/name', () => {
    const payload = buildRecorderPayload({
      token: '  tok  ',
      name: '  My tour  ',
      steps: [step({ selector: '#a', title: 'One', body: 'Body' })],
    });
    expect(payload).toEqual({
      token: 'tok',
      name: 'My tour',
      steps: [{ selector: '#a', title: 'One', body: 'Body' }],
    });
    expect('url_pattern' in payload).toBe(false);
  });

  it('omits empty/whitespace title and body so the backend fills defaults', () => {
    const payload = buildRecorderPayload({
      token: 't',
      name: 'n',
      steps: [step({ selector: '#a', title: '   ', body: '' })],
    });
    expect(payload.steps[0]).toEqual({ selector: '#a' });
  });

  it('includes url_pattern only when non-empty (trimmed)', () => {
    expect(
      buildRecorderPayload({ token: 't', name: 'n', urlPattern: '  ', steps: [] }),
    ).not.toHaveProperty('url_pattern');
    expect(
      buildRecorderPayload({
        token: 't',
        name: 'n',
        urlPattern: '  https://app/* ',
        steps: [],
      }).url_pattern,
    ).toBe('https://app/*');
  });

  it('preserves step order', () => {
    const payload = buildRecorderPayload({
      token: 't',
      name: 'n',
      steps: [
        step({ selector: '#1' }),
        step({ selector: '#2' }),
        step({ selector: '#3' }),
      ],
    });
    expect(payload.steps.map((s) => s.selector)).toEqual(['#1', '#2', '#3']);
  });
});

describe('suggestUrlPattern', () => {
  it('derives origin + path + wildcard and drops the query', () => {
    expect(suggestUrlPattern('https://app.example.com/dashboard?tab=1')).toBe(
      'https://app.example.com/dashboard*',
    );
  });

  it('returns an empty string for an unparseable URL', () => {
    expect(suggestUrlPattern('not a url')).toBe('');
  });
});
