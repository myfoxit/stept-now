/**
 * Extension translation runtime.
 *
 * Same catalog format and the same tiny engine as the dashboard, the widget and
 * the backend: flat dotted keys, `{{name}}` interpolation, `key_one`/`key_other`
 * plural siblings selected by CLDR rules (`Intl.PluralRules`). Deliberately so —
 * a string moved between surfaces never needs rewriting, and one `check-i18n`
 * guard polices all four runtimes.
 *
 * Unlike the dashboard there is no dynamic chunk loading: the catalogs are
 * small, and this code runs in three different bundles (side panel, content
 * scripts, service worker), so every catalog is imported statically and each
 * bundle carries what it uses. The locale is the browser's UI language,
 * detected once at startup — the extension has no language picker of its own.
 */

import de from './catalogs/de.json';
import en from './catalogs/en.json';
import es from './catalogs/es.json';
import fr from './catalogs/fr.json';
import it from './catalogs/it.json';
import { DEFAULT_LOCALE, normalizeLocale } from './locales';

export type Catalog = Record<string, string>;

const CATALOGS: Record<string, Catalog> = { en, de, fr, es, it };

/** Browser UI language: `chrome.i18n` in extension contexts, `navigator`
 * elsewhere (tests). Guarded — content scripts and jsdom differ in what
 * globals exist. */
function detectLocale(): string {
  let raw: string | undefined;
  try {
    if (typeof chrome !== 'undefined' && chrome.i18n?.getUILanguage) {
      raw = chrome.i18n.getUILanguage();
    }
  } catch {
    /* not an extension context */
  }
  if (!raw && typeof navigator !== 'undefined') raw = navigator.language;
  return normalizeLocale(raw) ?? DEFAULT_LOCALE;
}

let current: string = detectLocale();

export function getLocale(): string {
  return current;
}

/** Force a locale (tests, future settings UI). Unshipped tags fall back to English. */
export function setLocale(raw: string | null | undefined): string {
  current = normalizeLocale(raw) ?? DEFAULT_LOCALE;
  return current;
}

/** Seed or replace a catalog at runtime — used by tests. */
export function registerCatalog(locale: string, catalog: Catalog): void {
  const code = normalizeLocale(locale);
  if (code) CATALOGS[code] = catalog;
}

// --- lookup ----------------------------------------------------------------

const pluralRules = new Map<string, Intl.PluralRules>();

function categoryFor(locale: string, count: number): string {
  let rules = pluralRules.get(locale);
  if (!rules) {
    try {
      rules = new Intl.PluralRules(locale);
    } catch {
      rules = new Intl.PluralRules(DEFAULT_LOCALE);
    }
    pluralRules.set(locale, rules);
  }
  return rules.select(count);
}

function interpolate(template: string, vars?: Record<string, unknown>): string {
  if (!vars) return template;
  // An unknown placeholder stays visible: the sentence still reads, and the
  // gap is obvious in a screenshot.
  return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

/** The raw template for `key`: plural-sibling selection by `vars.count`, then
 * per-key English fallback. Null when no catalog knows the key. */
function template(key: string, vars?: Record<string, unknown>): string | null {
  const count = vars?.['count'];
  const candidates: string[] = [];
  if (typeof count === 'number' && Number.isFinite(count)) {
    candidates.push(`${key}_${categoryFor(current, count)}`, `${key}_other`);
  }
  candidates.push(key);

  for (const locale of current === DEFAULT_LOCALE ? [current] : [current, DEFAULT_LOCALE]) {
    const catalog = CATALOGS[locale];
    if (!catalog) continue;
    for (const candidate of candidates) {
      const found = catalog[candidate];
      if (found !== undefined) return found;
    }
  }
  return null;
}

/** Translate `key` in the active locale. Unknown keys render as the key itself. */
export function t(key: string, vars?: Record<string, unknown>): string {
  const found = template(key, vars);
  return found === null ? key : interpolate(found, vars);
}

/**
 * Split a translation around one placeholder, for strings that wrap a value in
 * markup: `{{count}} steps` rendered as `<b>3</b> steps`.
 *
 * Returning both halves keeps word order the translator's decision, which a
 * hardcoded `<>{prefix}<b>{x}</b></>` cannot. Plural selection still uses the
 * real `vars.count` — only the interpolation is deferred to the caller.
 */
export function tParts(
  key: string,
  placeholder: string,
  vars?: Record<string, unknown>,
): [string, string] {
  const token = `{{${placeholder}}}`;
  const found = template(key, vars) ?? key;
  const raw = interpolate(found, { ...vars, [placeholder]: token });
  const at = raw.indexOf(token);
  if (at === -1) return [raw, ''];
  return [raw.slice(0, at), raw.slice(at + token.length)];
}

export { DEFAULT_LOCALE, LOCALES, localeDirection, normalizeLocale, SUPPORTED_LOCALES } from './locales';
export type { Locale, LocaleInfo } from './locales';
