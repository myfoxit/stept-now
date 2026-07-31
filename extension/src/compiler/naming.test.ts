import { describe, expect, it } from 'vitest';
import { brandName, deriveTitle, humanUrl, preview, quote, slugify, targetName } from './naming';
import { target } from './fixtures';

describe('targetName', () => {
  it('prefers the accessible name, then visible text, then attributes', () => {
    expect(targetName(target('Save changes'))).toBe('Save changes');
    expect(
      targetName({
        selectors: [],
        text: { content: 'Delete' },
        fingerprint: { elementHash: 'h', stableHash: 's', tagPath: 'button', attrs: {}, neighborText: [] },
      }),
    ).toBe('Delete');
    expect(
      targetName({
        selectors: [],
        fingerprint: {
          elementHash: 'h',
          stableHash: 's',
          tagPath: 'input',
          attrs: { placeholder: 'Search projects' },
          neighborText: [],
        },
      }),
    ).toBe('Search projects');
  });

  it('never returns a raw selector — it falls back to a friendly role noun', () => {
    expect(
      targetName({
        selectors: [],
        aria: { role: 'searchbox' },
        fingerprint: {
          elementHash: 'h',
          stableHash: 's',
          tagPath: 'input',
          attrs: { 'aria-label': '[data-testid="x"]' },
          neighborText: [],
        },
      }),
    ).toBe('search field');
    expect(targetName({ selectors: [] })).toBe('element');
  });

  it('collapses whitespace and caps long labels', () => {
    const long = 'a'.repeat(60);
    expect(targetName({ selectors: [], text: { content: `  Save   now  ` } })).toBe('Save now');
    expect(targetName({ selectors: [], text: { content: long } })).toHaveLength(42);
  });
});

describe('brandName / humanUrl', () => {
  it('uses the brand table, else Title-Cases the second-level domain', () => {
    expect(brandName('github.com')).toBe('GitHub');
    expect(brandName('www.github.com')).toBe('GitHub');
    expect(brandName('app.acme-tools.io')).toBe('Acme Tools');
    expect(brandName(undefined)).toBeUndefined();
  });

  it('falls back to host + path for unbranded deep links', () => {
    expect(humanUrl('https://github.com/x')).toBe('GitHub');
    expect(humanUrl('not a url')).toBe('not a url');
  });
});

describe('slugify / deriveTitle / preview / quote', () => {
  it('slugifies to a bounded, url-safe token', () => {
    expect(slugify('Create your FIRST automation!')).toBe('create-your-first-automation');
    expect(slugify('!!!')).toBe('tour');
    expect(slugify('x'.repeat(80))).toHaveLength(48);
  });

  it('titles a recording from the brand and its first content step', () => {
    expect(deriveTitle(['Click on “New”'], ['github.com'])).toBe('GitHub — Click on “New”');
    expect(deriveTitle([], ['github.com'])).toBe('GitHub tour');
    expect(deriveTitle([], [])).toBe('Recorded tour');
  });

  it('previews long typed values with an ellipsis', () => {
    expect(preview('short')).toBe('short');
    expect(preview('x'.repeat(40))).toHaveLength(25);
    expect(quote('hi')).toBe('“hi”');
  });
});
