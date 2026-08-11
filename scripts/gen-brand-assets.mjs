#!/usr/bin/env node
/**
 * Regenerate every raster brand asset, plus the `.svg` favicons, from the one
 * source of truth: `assets/brand/` (see its README for which variant goes
 * where and why).
 *
 *   node scripts/gen-brand-assets.mjs
 *
 * Renders with the Chromium that Playwright already vendors (no system
 * imagemagick/librsvg needed). Writes:
 *   landing|frontend|docs-site/public : favicon.svg, favicon-32.png, apple-touch-icon.png
 *   landing/public                    : og.png (1200×630 social card)
 *   docs-site/public                  : logo.svg, logo-dark.svg (header lockup mark)
 *   extension/src/public/icon         : 16/48/128.png
 */
import { createRequire } from 'node:module'
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BRAND = join(ROOT, 'assets', 'brand')

// playwright lives in the pnpm store, not under scripts/; resolve it there so
// this runs with the repo's existing install (no extra dependency).
const require = createRequire(join(ROOT, 'e2e', 'package.json'))
let chromium
try {
  ;({ chromium } = require('playwright'))
} catch {
  ;({ chromium } = require(
    join(ROOT, 'node_modules/.pnpm/playwright@1.62.1/node_modules/playwright')
  ))
}

// The brand hexes, from assets/brand/README.md. Standalone asset files have no
// CSS token to inherit, so they carry the literal values; in-product marks
// (StepMark.tsx, Wordmark.astro, the extension's Logo) paint from the surface's
// own indigo token instead and are deliberately NOT generated from here.
const INK = '#14161A'
const PAPER = '#FFFFFF'
const INDIGO = '#4F46E5'
const INDIGO_DARK = '#6D66F0' // indigo on near-black only

/**
 * The mark: two bars on a 24×24 grid, the leading one stepped right and down.
 * `upper`/`lower` are explicit so each caller states its own palette — the one
 * place in the repo allowed to hard-code the mark's colours.
 */
function mark(upper, lower) {
  return `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <rect x="3" y="3.5" width="15" height="7" rx="2.9" fill="${upper}"/>
    <rect x="6" y="13.5" width="15" height="7" rx="2.9" fill="${lower}"/>
  </svg>`
}

/**
 * The tile for anything drawn at 32px or below — favicons and the Chrome
 * toolbar icon. Hand-snapped to the 16px pixel grid, because that is the size a
 * tab strip and a toolbar actually draw it at, and the scaled master lands on
 * half-pixels there and antialiases into two grey smudges. Everything is
 * integer: 10×4 bars, 2px gap, 2px step, 3px margins.
 *
 * Bars are proportionally chunkier than the master (25% of the height each
 * against 29%, but 62.5% of the width against 62.5% at a much smaller margin) —
 * the same optical correction `assets/brand/stept-favicon.svg` makes, taken one
 * step further because this one sits on a filled tile.
 */
function tileSmall() {
  return `<svg viewBox="0 0 16 16" width="32" height="32" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Stept">
  <title>Stept</title>
  <rect width="16" height="16" rx="3.5" fill="${INDIGO}"/>
  <rect x="2" y="3" width="10" height="4" rx="2" fill="${PAPER}"/>
  <rect x="4" y="9" width="10" height="4" rx="2" fill="${PAPER}"/>
</svg>`
}

/**
 * The app-icon tile at 33px and up: exactly `assets/brand/stept-app-icon.svg` —
 * indigo field, white master mark, the kit's 22.3% corner radius (114/512) and
 * 21.9% inset (112/512) for the maskable safe zone.
 */
function tileLarge(px) {
  const inset = px * 0.219
  const size = px - inset * 2
  return `<svg viewBox="0 0 ${px} ${px}" width="${px}" height="${px}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Stept">
  <title>Stept</title>
  <rect width="${px}" height="${px}" rx="${+((px * 114) / 512).toFixed(2)}" fill="${INDIGO}"/>
  <svg x="${+inset.toFixed(2)}" y="${+inset.toFixed(2)}" width="${+size.toFixed(2)}" height="${+size.toFixed(2)}" viewBox="0 0 24 24">
    <rect x="3" y="3.5" width="15" height="7" rx="2.9" fill="${PAPER}"/>
    <rect x="6" y="13.5" width="15" height="7" rx="2.9" fill="${PAPER}"/>
  </svg>
</svg>`
}

/** Indigo tile, white mark — for every icon whose backdrop we do not control. */
function tileSvg(px) {
  return px <= 32 ? tileSmall() : tileLarge(px)
}

/** The same tile, wrapped for a Playwright screenshot. The explicit width and
 *  height matter: `tileSmall` carries an intrinsic 32px size, so at the 16px
 *  viewport the screenshot would otherwise clip its top-left quarter. */
function tile(px) {
  return `<!doctype html><meta charset="utf-8"><style>
    html,body{margin:0} svg{display:block;width:${px}px;height:${px}px}</style>${tileSvg(px)}`
}

/** Self-hosted Instrument Sans, so the social card uses the real display face
 *  rather than whatever Chromium falls back to. Inlined as a data URI because
 *  the page is `setContent`, which has no base URL to resolve @font-face from. */
function displayFontCss() {
  try {
    const woff2 = require.resolve(
      '@fontsource-variable/instrument-sans/files/instrument-sans-latin-wght-normal.woff2'
    )
    const b64 = readFileSync(woff2).toString('base64')
    return `@font-face{font-family:'Instrument Sans Variable';font-style:normal;
      font-weight:400 700;src:url(data:font/woff2;base64,${b64}) format('woff2-variations')}`
  } catch {
    console.warn('! Instrument Sans not resolvable — og.png falls back to a system face')
    return ''
  }
}

/** The 1200×630 OG card: ink field, split mark + wordmark, headline. */
function ogCard() {
  return `<!doctype html><meta charset="utf-8"><style>
    ${displayFontCss()}
    html,body{margin:0}
    #c{width:1200px;height:630px;position:relative;overflow:hidden;
      background:radial-gradient(120% 120% at 78% 18%, #241d5e 0%, #0B0C0F 55%);
      font-family:'Instrument Sans Variable',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      color:${PAPER}}
    .brand{position:absolute;top:70px;left:80px;display:flex;align-items:center;gap:18px}
    .brand svg{width:54px;height:54px}
    .brand span{font-size:42px;font-weight:600;letter-spacing:-.03em}
    h1{position:absolute;top:225px;left:80px;margin:0;width:640px;font-size:74px;line-height:1.04;
      font-weight:700;letter-spacing:-.032em}
    p{position:absolute;top:430px;left:80px;margin:0;width:720px;font-size:29px;color:#A1A1AA;font-weight:500}
    .u{position:absolute;top:520px;left:80px;font-size:26px;font-weight:600;color:${INDIGO_DARK}}
    /* A watermark, not a second logo: mono, low-contrast, and whole. Cropping
       it off the corner (as this did before) leaves two loose rounded shapes
       that never resolve into a mark, which is worse than no mark at all. */
    .big{position:absolute;right:78px;top:160px;width:340px;height:340px;opacity:.14}
    .big svg{width:100%;height:100%}
  </style>
  <div id="c">
    <div class="brand">${mark(PAPER, INDIGO_DARK)}<span>Stept</span></div>
    <h1>Customer support that resolves itself.</h1>
    <p>Open-source Intercom + Fin — with an agent that shows and does</p>
    <div class="u">stepped.ai</div>
    <div class="big">${mark(PAPER, PAPER)}</div>
  </div>`
}

/** The docs header mark: copied from the kit, never redrawn. Starlight swaps
 *  the two by theme, so each gets the palette for its surface. */
const COPY_JOBS = [
  { from: 'stept-mark-partial.svg', to: ['docs-site/public/logo.svg'] },
  { from: 'stept-mark-partial-dark.svg', to: ['docs-site/public/logo-dark.svg'] },
]

/**
 * The `.svg` favicon is the tile, matching `favicon-32.png` exactly.
 *
 * The kit's `stept-favicon.svg` is transparent indigo bars, and pairing it with
 * a tiled PNG fallback would mean Chrome and Firefox showing one icon while
 * everything without SVG favicon support shows a different one. Beyond the
 * inconsistency, bare indigo bars sit at roughly 2.4:1 against Chrome's dark
 * tab strip — legible, but muddy in a row of twenty tabs. The tile is the
 * container the brand does sanction, so both formats use it and the kit file
 * stays in `assets/brand/` for hand-off.
 */
const SVG_JOBS = [
  {
    svg: tileSmall(),
    to: [
      'landing/public/favicon.svg',
      'frontend/public/favicon.svg',
      'docs-site/public/favicon.svg',
    ],
  },
]

const PNG_JOBS = [
  {
    html: tile(32),
    out: [
      'landing/public/favicon-32.png',
      'frontend/public/favicon-32.png',
      'docs-site/public/favicon-32.png',
    ],
    w: 32,
    h: 32,
  },
  {
    html: tile(180),
    out: [
      'landing/public/apple-touch-icon.png',
      'frontend/public/apple-touch-icon.png',
      'docs-site/public/apple-touch-icon.png',
    ],
    w: 180,
    h: 180,
  },
  { html: tile(16), out: ['extension/src/public/icon/16.png'], w: 16, h: 16 },
  { html: tile(48), out: ['extension/src/public/icon/48.png'], w: 48, h: 48 },
  { html: tile(128), out: ['extension/src/public/icon/128.png'], w: 128, h: 128 },
  { html: ogCard(), out: ['landing/public/og.png'], w: 1200, h: 630 },
]

for (const job of COPY_JOBS) {
  for (const rel of job.to) {
    const abs = join(ROOT, rel)
    mkdirSync(dirname(abs), { recursive: true })
    copyFileSync(join(BRAND, job.from), abs)
    console.log(`wrote ${rel} (from assets/brand/${job.from})`)
  }
}

for (const job of SVG_JOBS) {
  for (const rel of job.to) {
    const abs = join(ROOT, rel)
    mkdirSync(dirname(abs), { recursive: true })
    writeFileSync(abs, `${job.svg}\n`)
    console.log(`wrote ${rel} (tile)`)
  }
}

const browser = await chromium.launch()
try {
  for (const job of PNG_JOBS) {
    const page = await browser.newPage({
      viewport: { width: job.w, height: job.h },
      deviceScaleFactor: 1,
    })
    await page.setContent(job.html, { waitUntil: 'networkidle' })
    await page.evaluate(() => document.fonts.ready)
    const png = await page.screenshot({
      omitBackground: true,
      clip: { x: 0, y: 0, width: job.w, height: job.h },
    })
    await page.close()
    for (const rel of job.out) {
      const abs = join(ROOT, rel)
      mkdirSync(dirname(abs), { recursive: true })
      writeFileSync(abs, png)
      console.log(`wrote ${rel} (${job.w}×${job.h})`)
    }
  }
} finally {
  await browser.close()
}
