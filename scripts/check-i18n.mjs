#!/usr/bin/env node
/**
 * Catalog integrity guard.
 *
 * Translations arrive as pull requests from people who cannot run the app in
 * their language, so the build has to be the reviewer. This checks the things a
 * human reviewer of a 400-line JSON diff reliably misses:
 *
 * 1. every locale defines every key English defines (compared on the base key,
 *    since plural *forms* legitimately differ per language);
 * 2. every locale supplies the plural categories its own CLDR rules can
 *    actually produce — a Polish catalog with only `_one`/`_other` renders
 *    "5 gwiazdki" instead of "5 gwiazdek";
 * 3. no translation interpolates a `{{variable}}` English does not provide,
 *    which would otherwise render as literal braces in production;
 * 4. no locale defines keys English has dropped (dead weight that silently
 *    survives refactors);
 * 5. the locale registry is identical across the four runtimes that each keep
 *    their own copy.
 *
 * Exit code 1 on any failure, so it can gate CI.
 */

import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

/**
 * Catalog sets to check.
 *
 * `minCoverage` is the fraction of English keys each locale must define. It is
 * 1 everywhere a catalog is small enough to keep complete, and lower for the
 * dashboard, whose ~1,000 keys are being translated in waves. A gap there is
 * not a bug — `t` falls back to English key by key — but it must be *measured*,
 * because the failure mode of a partially translated app is that nobody notices
 * which parts are missing. Raise the floor as waves land; never lower it.
 */
const CATALOG_SETS = [
  { label: 'backend', dir: join(ROOT, 'backend/app/i18n'), minCoverage: 1 },
  { label: 'widget', dir: join(ROOT, 'widget/public/i18n'), minCoverage: 1 },
  { label: 'dashboard', dir: join(ROOT, 'frontend/src/i18n/catalogs'), minCoverage: 0.16 },
  { label: 'extension', dir: join(ROOT, 'extension/src/i18n/catalogs'), minCoverage: 1 },
]

/** Files that each keep a copy of the locale registry. */
const REGISTRY_MIRRORS = [
  join(ROOT, 'widget/src/i18n/locales.ts'),
  join(ROOT, 'frontend/src/i18n/locales.ts'),
  join(ROOT, 'extension/src/i18n/locales.ts'),
]

const SOURCE_LOCALE = 'en'
const PLURAL_CATEGORIES = new Set(['zero', 'one', 'two', 'few', 'many', 'other'])

const failures = []
const fail = (message) => failures.push(message)
/** [set, locale, coverage, missingCount] — printed as a table at the end. */
const coverageRows = []

/** Strip a trailing plural category so `csat.star_one` and `csat.star_few` agree. */
function baseKey(key) {
  const at = key.lastIndexOf('_')
  if (at === -1) return key
  return PLURAL_CATEGORIES.has(key.slice(at + 1)) ? key.slice(0, at) : key
}

function placeholders(text) {
  return new Set([...String(text).matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]))
}

/** Plural categories `locale` can actually produce for realistic counts. */
function categoriesUsedBy(locale) {
  const rules = new Intl.PluralRules(locale)
  const seen = new Set()
  for (let n = 0; n <= 200; n++) seen.add(rules.select(n))
  return seen
}

function readCatalog(path) {
  const raw = JSON.parse(readFileSync(path, 'utf8'))
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('catalog is not a JSON object')
  }
  return raw
}

function checkCatalogSet({ label, dir, minCoverage = 1 }) {
  if (!existsSync(dir)) {
    // A surface that has not been localised yet is not a failure; a surface
    // that is half-localised is, and that shows up as missing keys below.
    console.log(`  ${label}: no catalogs (skipped)`)
    return
  }
  const files = readdirSync(dir).filter((f) => f.endsWith('.json'))
  const locales = files.map((f) => f.replace(/\.json$/, ''))

  if (!locales.includes(SOURCE_LOCALE)) {
    fail(`${label}: no ${SOURCE_LOCALE}.json to compare against`)
    return
  }

  const catalogs = {}
  for (const locale of locales) {
    try {
      catalogs[locale] = readCatalog(join(dir, `${locale}.json`))
    } catch (err) {
      fail(`${label}/${locale}: ${err.message}`)
    }
  }

  const source = catalogs[SOURCE_LOCALE]
  if (!source) return
  const sourceBases = new Set(Object.keys(source).map(baseKey))

  // Plural keys are the ones English spells with a category suffix.
  const pluralBases = new Set(
    Object.keys(source)
      .filter((k) => PLURAL_CATEGORIES.has(k.slice(k.lastIndexOf('_') + 1)))
      .map(baseKey),
  )

  for (const locale of locales) {
    const catalog = catalogs[locale]
    if (!catalog) continue

    for (const [key, value] of Object.entries(catalog)) {
      if (typeof value !== 'string') fail(`${label}/${locale}: "${key}" is not a string`)
    }

    const bases = new Set(Object.keys(catalog).map(baseKey))

    const missing = [...sourceBases].filter((base) => !bases.has(base))
    const coverage = 1 - missing.length / Math.max(sourceBases.size, 1)
    if (locale !== SOURCE_LOCALE) {
      coverageRows.push([label, locale, coverage, missing.length])
      if (coverage < minCoverage) {
        fail(
          `${label}/${locale}: ${(coverage * 100).toFixed(1)}% translated, ` +
            `below the ${(minCoverage * 100).toFixed(0)}% floor ` +
            `(${missing.length} keys fall back to English, e.g. "${missing[0]}")`,
        )
      }
    }
    for (const base of bases) {
      if (!sourceBases.has(base)) fail(`${label}/${locale}: unknown key "${base}" (not in English)`)
    }

    if (locale !== SOURCE_LOCALE) {
      // Plural completeness applies only to keys this locale actually defines:
      // a key it has not translated yet falls back to English wholesale, forms
      // and all, so demanding its plural forms would be nonsense.
      let categories
      try {
        categories = categoriesUsedBy(locale)
      } catch {
        fail(`${label}/${locale}: not a locale Intl.PluralRules recognises`)
        categories = new Set(['other'])
      }
      for (const base of pluralBases) {
        if (!bases.has(base)) continue // untranslated: falls back to English
        for (const category of categories) {
          if (catalog[`${base}_${category}`] === undefined) {
            fail(
              `${label}/${locale}: "${base}" has no _${category} form ` +
                `(${locale} uses: ${[...categories].sort().join(', ')})`,
            )
          }
        }
      }
    }

    for (const [key, value] of Object.entries(catalog)) {
      if (typeof value !== 'string') continue
      const sourceText = source[key] ?? source[`${baseKey(key)}_other`] ?? source[baseKey(key)]
      if (sourceText === undefined) continue
      const allowed = placeholders(sourceText)
      for (const name of placeholders(value)) {
        if (!allowed.has(name)) {
          fail(`${label}/${locale}: "${key}" interpolates {{${name}}}, which English does not provide`)
        }
      }
    }
  }

  console.log(`  ${label}: ${locales.length} locales, ${sourceBases.size} keys`)
}

/** All registry mirrors must list the same locale codes, in the same order. */
function checkRegistryMirrors() {
  const present = REGISTRY_MIRRORS.filter(existsSync)
  if (present.length < 2) return

  const extract = (path) => {
    const text = readFileSync(path, 'utf8')
    return [...text.matchAll(/code:\s*'([^']+)'/g)].map((m) => m[1])
  }

  const [first, ...rest] = present
  const expected = extract(first)
  if (expected.length === 0) {
    fail(`registry: could not parse locale codes out of ${first}`)
    return
  }
  for (const path of rest) {
    const actual = extract(path)
    if (actual.join(',') !== expected.join(',')) {
      fail(
        `registry drift: ${path} lists [${actual.join(', ')}] but ` +
          `${first} lists [${expected.join(', ')}]`,
      )
    }
  }
  console.log(`  registry: ${present.length} mirrors agree on ${expected.length} locales`)
}

console.log('Checking i18n catalogs…')
for (const set of CATALOG_SETS) checkCatalogSet(set)
checkRegistryMirrors()

if (coverageRows.length > 0) {
  console.log('\nTranslation coverage')
  const bySet = new Map()
  for (const [set, locale, coverage, missing] of coverageRows) {
    if (!bySet.has(set)) bySet.set(set, [])
    bySet.get(set).push({ locale, coverage, missing })
  }
  for (const [set, rows] of bySet) {
    const complete = rows.filter((row) => row.missing === 0).length
    console.log(`  ${set}: ${complete}/${rows.length} locales complete`)
    for (const row of rows.filter((entry) => entry.missing > 0)) {
      const bar = '█'.repeat(Math.round(row.coverage * 20)).padEnd(20, '·')
      console.log(
        `    ${row.locale.padEnd(6)} ${bar} ${(row.coverage * 100).toFixed(0).padStart(3)}%` +
          `  (${row.missing} keys fall back to English)`,
      )
    }
  }
}

if (failures.length > 0) {
  console.error(`\n✗ ${failures.length} i18n problem(s):\n`)
  for (const message of failures) console.error(`  - ${message}`)
  process.exit(1)
}
console.log('\n✓ i18n catalogs are consistent')
