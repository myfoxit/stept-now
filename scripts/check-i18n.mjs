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
 * `minCoverage` is the fraction of English keys each locale must define, and it
 * is 1 everywhere. That is the whole point of shipping five languages instead of
 * thirteen: "Stept speaks German" has to mean every German string exists, on
 * every surface. `t` still falls back to English key by key, so a gap degrades
 * rather than crashes — but a half-translated screen is precisely the failure
 * nobody reports, so the build refuses it instead of measuring it. Never lower a
 * floor to make a wave pass; finish the wave, or drop the locale.
 */
const CATALOG_SETS = [
  { label: 'backend', dir: join(ROOT, 'backend/app/i18n'), minCoverage: 1 },
  { label: 'widget', dir: join(ROOT, 'widget/public/i18n'), minCoverage: 1 },
  { label: 'dashboard', dir: join(ROOT, 'frontend/src/i18n/catalogs'), minCoverage: 1 },
  { label: 'extension', dir: join(ROOT, 'extension/src/i18n/catalogs'), minCoverage: 1 },
  { label: 'landing', dir: join(ROOT, 'landing/src/i18n'), minCoverage: 1 },
]

/** Files that each keep a copy of the locale registry. */
const REGISTRY_MIRRORS = [
  join(ROOT, 'widget/src/i18n/locales.ts'),
  join(ROOT, 'frontend/src/i18n/locales.ts'),
  join(ROOT, 'extension/src/i18n/locales.ts'),
  join(ROOT, 'landing/src/i18n/index.ts'),
]

const SOURCE_LOCALE = 'en'
const PLURAL_CATEGORIES = new Set(['zero', 'one', 'two', 'few', 'many', 'other'])

const failures = []
const fail = (message) => failures.push(message)
/** [set, locale, coverage, missingCount] — printed as a table at the end. */
const coverageRows = []

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

  // Which keys are *really* plurals? English spells a plural with every category
  // it uses, so a genuine one appears under two or more suffixes (`_one` **and**
  // `_other`). A lone `_one` is a slug whose English text merely ends in the word
  // "one" — "Create one" was extracted as `ai.create_one` — and reading that as a
  // plural makes the guard demand an `_other` twin from every translator, for a
  // form nothing will ever render.
  const sourceCategories = new Map()
  for (const key of Object.keys(source)) {
    const at = key.lastIndexOf('_')
    if (at === -1) continue
    const category = key.slice(at + 1)
    if (!PLURAL_CATEGORIES.has(category)) continue
    const base = key.slice(0, at)
    if (!sourceCategories.has(base)) sourceCategories.set(base, new Set())
    sourceCategories.get(base).add(category)
  }
  const pluralBases = new Set(
    [...sourceCategories].filter(([, cats]) => cats.size >= 2).map(([base]) => base),
  )

  /** Collapse a plural sibling onto its base; leave every other key untouched. */
  const baseKey = (key) => {
    const at = key.lastIndexOf('_')
    if (at === -1) return key
    const base = key.slice(0, at)
    return PLURAL_CATEGORIES.has(key.slice(at + 1)) && pluralBases.has(base) ? base : key
  }

  const sourceBases = new Set(Object.keys(source).map(baseKey))

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

  // Two shapes declare the same thing: the three app runtimes list
  // `{ code: 'de', … }` records, the landing keeps a bare `locales = ['en', …]`
  // tuple. Both have to be readable here — a landing that quietly disagrees is a
  // locale the product offers and the marketing site has no page for.
  const extract = (path) => {
    const text = readFileSync(path, 'utf8')
    const records = [...text.matchAll(/code:\s*'([^']+)'/g)].map((m) => m[1])
    if (records.length > 0) return records
    const tuple = text.match(/locales\s*=\s*\[([^\]]*)\]/)
    return tuple ? [...tuple[1].matchAll(/'([^']+)'/g)].map((m) => m[1]) : []
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
  return expected
}

/**
 * The extension's *manifest* catalogs — a different mechanism from
 * `extension/src/i18n/`, and a harsher one.
 *
 * Chrome reads the name, description, toolbar tooltip and shortcut label before
 * any of our code runs, resolving `__MSG_key__` against
 * `_locales/<ui language>/messages.json`. If a referenced key is missing from the
 * `default_locale` catalog, Chrome does not fall back or render the key — it
 * refuses to load the extension. So this is checked as a build failure, not a
 * coverage percentage.
 */
function checkExtensionManifestLocales(shipped) {
  const config = join(ROOT, 'extension/wxt.config.ts')
  const localesDir = join(ROOT, 'extension/src/public/_locales')
  if (!existsSync(config) || !existsSync(localesDir)) return

  const text = readFileSync(config, 'utf8')
  // Only quoted placeholders count. Matching bare `__MSG_x__` would also pick up
  // the ones named in prose in this file's own comments.
  const referenced = new Set([...text.matchAll(/['"]__MSG_(\w+)__['"]/g)].map((m) => m[1]))
  if (referenced.size === 0) return

  const defaultLocale = text.match(/default_locale:\s*'([^']+)'/)?.[1]
  if (!defaultLocale) {
    fail('extension manifest: uses __MSG_ placeholders but sets no default_locale')
    return
  }

  // Chrome spells regional locales with an underscore (`pt_BR`), unlike BCP-47.
  const dirFor = (locale) => locale.replace(/-/g, '_')
  const expected = shipped?.length ? shipped : [defaultLocale]

  for (const locale of expected) {
    const path = join(localesDir, dirFor(locale), 'messages.json')
    if (!existsSync(path)) {
      fail(`extension manifest: no _locales/${dirFor(locale)}/messages.json for shipped locale ${locale}`)
      continue
    }
    let messages
    try {
      messages = JSON.parse(readFileSync(path, 'utf8'))
    } catch (err) {
      fail(`extension manifest/${locale}: ${err.message}`)
      continue
    }
    for (const key of referenced) {
      const entry = messages[key]
      if (entry === undefined) {
        fail(`extension manifest/${locale}: missing "${key}" (the manifest references __MSG_${key}__)`)
      } else if (typeof entry?.message !== 'string' || entry.message.trim() === '') {
        fail(`extension manifest/${locale}: "${key}" has no message string`)
      }
    }
    for (const key of Object.keys(messages)) {
      if (!referenced.has(key)) {
        fail(`extension manifest/${locale}: unused key "${key}" (nothing references __MSG_${key}__)`)
      }
    }
  }
  console.log(
    `  extension manifest: ${expected.length} locales define ${referenced.size} __MSG_ keys`,
  )
}

console.log('Checking i18n catalogs…')
for (const set of CATALOG_SETS) checkCatalogSet(set)
checkExtensionManifestLocales(checkRegistryMirrors())

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
