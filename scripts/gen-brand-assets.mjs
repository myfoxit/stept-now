#!/usr/bin/env node
/**
 * Regenerate every raster brand asset from the one source-of-truth mark
 * (the internal `stepped` "Blocks" lockup — see StepMark.tsx / Wordmark.astro).
 *
 *   node scripts/gen-brand-assets.mjs
 *
 * Renders with the Chromium that Playwright already vendors (no system
 * imagemagick/librsvg needed). Writes:
 *   landing|frontend|docs-site/public : favicon-32.png, apple-touch-icon.png
 *   landing/public                    : og.png (1200×630 social card)
 *   extension/src/public/icon         : 16/48/128.png
 */
import { createRequire } from 'node:module'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

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
const INK = '#09090b'
const PAPER = '#fafafa'
const INDIGO = '#5b46e5'

/** The mark as inline SVG. `mode` picks the palette for its surface. */
function mark(mode) {
  const stroke = mode === 'on-dark' ? PAPER : INK
  return `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M10 21V8a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H3"
      stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <rect x="14" y="3" width="7" height="7" rx="1.4" fill="${INDIGO}"/>
  </svg>`
}

/** A rounded-tile icon (favicon / touch / extension): ink tile, white mark. */
function tile(px, pad = 0.16, radius = 0.22) {
  const inset = Math.round(px * pad)
  const size = px - inset * 2
  return `<!doctype html><meta charset="utf-8"><style>
    html,body{margin:0} #c{width:${px}px;height:${px}px;background:${INK};
      border-radius:${Math.round(px * radius)}px;display:flex;align-items:center;justify-content:center}
    svg{width:${size}px;height:${size}px}</style>
    <div id="c">${mark('on-dark')}</div>`
}

/** The 1200×630 OG card: ink field, wordmark top-left, headline, big mark. */
function ogCard() {
  return `<!doctype html><meta charset="utf-8"><style>
    html,body{margin:0}
    #c{width:1200px;height:630px;position:relative;overflow:hidden;
      background:radial-gradient(120% 120% at 78% 18%, #241d5e 0%, ${INK} 55%);
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:${PAPER}}
    .brand{position:absolute;top:70px;left:80px;display:flex;align-items:center;gap:16px}
    .brand svg{width:52px;height:52px}
    .brand span{font-size:40px;font-weight:700;letter-spacing:-.03em}
    h1{position:absolute;top:225px;left:80px;margin:0;width:640px;font-size:74px;line-height:1.04;
      font-weight:700;letter-spacing:-.03em}
    p{position:absolute;top:430px;left:80px;margin:0;width:720px;font-size:29px;color:#a1a1aa;font-weight:500}
    .u{position:absolute;top:520px;left:80px;font-size:26px;font-weight:600;color:${INDIGO}}
    .big{position:absolute;right:-40px;top:150px;width:520px;height:520px;opacity:.95}
    .big svg{width:100%;height:100%}
  </style>
  <div id="c">
    <div class="brand">${mark('on-dark')}<span>Stept</span></div>
    <h1>Customer support that resolves itself.</h1>
    <p>Open-source Intercom + Fin — with an agent that shows and does</p>
    <div class="u">stepped.ai</div>
    <div class="big">${mark('on-dark')}</div>
  </div>`
}

const JOBS = [
  { html: tile(32), out: ['landing/public/favicon-32.png', 'frontend/public/favicon-32.png', 'docs-site/public/favicon-32.png'], w: 32, h: 32 },
  { html: tile(180), out: ['landing/public/apple-touch-icon.png', 'frontend/public/apple-touch-icon.png'], w: 180, h: 180 },
  { html: tile(16, 0.12, 0.2), out: ['extension/src/public/icon/16.png'], w: 16, h: 16 },
  { html: tile(48), out: ['extension/src/public/icon/48.png'], w: 48, h: 48 },
  { html: tile(128), out: ['extension/src/public/icon/128.png'], w: 128, h: 128 },
  { html: ogCard(), out: ['landing/public/og.png'], w: 1200, h: 630 },
]

const browser = await chromium.launch()
try {
  for (const job of JOBS) {
    const page = await browser.newPage({ viewport: { width: job.w, height: job.h }, deviceScaleFactor: 1 })
    await page.setContent(job.html, { waitUntil: 'networkidle' })
    const png = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: job.w, height: job.h } })
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
