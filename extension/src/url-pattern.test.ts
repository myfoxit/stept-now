import { describe, expect, it } from 'vitest';
import {
  isInternalUrl,
  stepPageUrl,
  suggestUrlPattern,
  urlPatternOf,
  wildcardMatch,
} from './url-pattern';

describe('suggestUrlPattern', () => {
  it('suggests "this page and anything under it"', () => {
    expect(suggestUrlPattern('https://app.example.com/settings/billing')).toBe(
      'https://app.example.com/settings/billing*',
    );
  });

  it('drops the query string and a trailing slash', () => {
    expect(suggestUrlPattern('https://app.example.com/inbox/?tab=open')).toBe(
      'https://app.example.com/inbox*',
    );
    expect(suggestUrlPattern('https://app.example.com/')).toBe('https://app.example.com*');
  });

  it('returns empty for non-http and unparseable urls (tour stays manual)', () => {
    expect(suggestUrlPattern('chrome://newtab')).toBe('');
    expect(suggestUrlPattern('not a url')).toBe('');
    expect(suggestUrlPattern('')).toBe('');
  });
});

describe('urlPatternOf', () => {
  it('wildcards trailing ids so a per-record page generalizes', () => {
    expect(urlPatternOf('https://app.example.com/d/9f2c1a7b/edit')).toBe(
      'https://app.example.com/d/*/edit*',
    );
    expect(urlPatternOf('https://app.example.com/home')).toBe('https://app.example.com/home*');
  });
});

describe('wildcardMatch', () => {
  it('honours * and ? with regex metacharacters escaped', () => {
    expect(wildcardMatch('https://a.example/*', 'https://a.example/path?q=1')).toBe(true);
    expect(wildcardMatch('https://a.example/x', 'https://a.example/y')).toBe(false);
    expect(wildcardMatch('*.example/settings', 'https://b.example/settings')).toBe(true);
    expect(wildcardMatch('https://a.example/x?', 'https://a.example/xy')).toBe(true);
    expect(wildcardMatch('https://a.example/x?', 'https://a.example/xyz')).toBe(false);
  });

  it('is case-insensitive, matching the widget after the wave-7 unification', () => {
    expect(wildcardMatch('https://APP.example.com/*', 'https://app.example.com/x')).toBe(true);
  });

  it('never matches an empty pattern', () => {
    expect(wildcardMatch('', 'https://a.example')).toBe(false);
  });
});

describe('isInternalUrl', () => {
  it('rejects browser-internal pages so they never become steps', () => {
    expect(isInternalUrl('chrome://newtab')).toBe(true);
    expect(isInternalUrl('about:blank')).toBe(true);
    expect(isInternalUrl('chrome-extension://abc/page.html')).toBe(true);
    expect(isInternalUrl(undefined)).toBe(true);
    expect(isInternalUrl('https://app.example.com')).toBe(false);
  });
});

describe('stepPageUrl', () => {
  it('keeps origin+path and drops query/hash', () => {
    expect(stepPageUrl('https://app.example.com/settings/billing?tab=cards#top')).toBe(
      'https://app.example.com/settings/billing',
    );
  });

  it('is null for internal, non-http and unparseable urls', () => {
    expect(stepPageUrl('chrome://newtab')).toBeNull();
    expect(stepPageUrl('about:blank')).toBeNull();
    expect(stepPageUrl('file:///tmp/x.html')).toBeNull();
    expect(stepPageUrl('not a url')).toBeNull();
    expect(stepPageUrl(undefined)).toBeNull();
  });
});
