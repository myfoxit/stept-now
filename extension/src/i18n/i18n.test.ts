import { beforeEach, describe, expect, it } from 'vitest';
import { getLocale, normalizeLocale, registerCatalog, setLocale, t, tParts } from './index';

describe('normalizeLocale', () => {
  it('accepts shipped locales and canonicalises case/underscores', () => {
    expect(normalizeLocale('en')).toBe('en');
    expect(normalizeLocale('DE')).toBe('de');
    expect(normalizeLocale('en_US')).toBe('en');
  });

  it('falls back from region to base language', () => {
    expect(normalizeLocale('de-AT')).toBe('de');
    expect(normalizeLocale('fr-CA')).toBe('fr');
    expect(normalizeLocale('es-419')).toBe('es');
  });

  it('returns null for unshipped or malformed tags', () => {
    expect(normalizeLocale('pt-BR')).toBeNull(); // trimmed out of the shipped set
    expect(normalizeLocale('zz')).toBeNull();
    expect(normalizeLocale('not a tag')).toBeNull();
    expect(normalizeLocale('')).toBeNull();
    expect(normalizeLocale(undefined)).toBeNull();
  });
});

describe('setLocale', () => {
  beforeEach(() => {
    setLocale('en');
  });

  it('applies a shipped locale and defaults unshipped ones to English', () => {
    expect(setLocale('de')).toBe('de');
    expect(getLocale()).toBe('de');
    expect(setLocale('ja')).toBe('en'); // not shipped by the extension
    expect(setLocale(null)).toBe('en');
  });
});

describe('t', () => {
  beforeEach(() => {
    setLocale('en');
  });

  it('returns the English catalog value', () => {
    expect(t('sidepanel.start_recording')).toBe('Start recording');
  });

  it('echoes unknown keys as a last resort', () => {
    expect(t('nope.not_a_key')).toBe('nope.not_a_key');
  });

  it('interpolates {{placeholders}} and leaves unknown ones visible', () => {
    expect(t('drive.driving_step', { current: 2, total: 5 })).toBe('Driving step 2 of 5');
    expect(t('drive.driving_step', { current: 2 })).toBe('Driving step 2 of {{total}}');
  });

  it('falls back to English per key when the locale catalog is missing it', () => {
    registerCatalog('de', { 'sidepanel.pause': 'Pausieren' });
    setLocale('de');
    expect(t('sidepanel.pause')).toBe('Pausieren');
    expect(t('sidepanel.stop')).toBe('Stop'); // untranslated → English
    registerCatalog('de', {});
  });

  it('selects the _one/_other plural form from vars.count', () => {
    expect(t('save.hint', { count: 1 })).toBe(
      '1 step will be saved. You can edit and publish it in the Stept dashboard.',
    );
    expect(t('save.hint', { count: 3 })).toBe(
      '3 steps will be saved. You can edit and publish it in the Stept dashboard.',
    );
  });

  it('uses the locale’s own CLDR rules (French: 0 is singular)', () => {
    registerCatalog('fr', {
      'tours.step_count_one': '{{count}} étape',
      'tours.step_count_other': '{{count}} étapes',
    });
    setLocale('fr');
    expect(t('tours.step_count', { count: 0 })).toBe('0 étape');
    expect(t('tours.step_count', { count: 1 })).toBe('1 étape');
    expect(t('tours.step_count', { count: 2 })).toBe('2 étapes');
    registerCatalog('fr', {});
  });
});

describe('tParts', () => {
  beforeEach(() => {
    setLocale('en');
  });

  it('splits around the placeholder so the caller can wrap the value in markup', () => {
    expect(tParts('sidepanel.step_count', 'count', { count: 3 })).toEqual(['', ' steps']);
    expect(tParts('sidepanel.step_count', 'count', { count: 1 })).toEqual(['', ' step']);
  });

  it('keeps plural selection working while deferring the count itself', () => {
    registerCatalog('de', {
      'sidepanel.step_count_one': 'Nur {{count}} Schritt',
      'sidepanel.step_count_other': 'Alle {{count}} Schritte',
    });
    setLocale('de');
    expect(tParts('sidepanel.step_count', 'count', { count: 1 })).toEqual(['Nur ', ' Schritt']);
    expect(tParts('sidepanel.step_count', 'count', { count: 4 })).toEqual(['Alle ', ' Schritte']);
    registerCatalog('de', {});
  });

  it('returns the whole string as the prefix when the placeholder is absent', () => {
    expect(tParts('sidepanel.stop', 'count')).toEqual(['Stop', '']);
  });
});
