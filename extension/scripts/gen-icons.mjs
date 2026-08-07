#!/usr/bin/env node
/**
 * The extension's toolbar icons (16/48/128) are generated from the shared Stept
 * mark alongside every other raster brand asset. This file used to hand-roll an
 * "S" glyph; that predates the brand and would overwrite the real mark, so it
 * now just delegates.
 *
 *   node ../scripts/gen-brand-assets.mjs      (from the repo root)
 */
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const shared = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'gen-brand-assets.mjs')
await import(shared)
