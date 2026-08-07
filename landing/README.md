# Stept landing

Marketing landing page for Stept — the open-source Intercom + Fin alternative. Built with
[Astro](https://astro.build) as a static site, isolated from the pnpm workspace (its own
`node_modules` and lockfile).

It ports the app's design tokens verbatim from `frontend/src/index.css` (same oklch palette,
0.5rem radius, indigo primary, dark mode via `prefers-color-scheme`) and reuses the app's
wordmark, so the site and the product read as one system. Zero external requests — icons are
inline SVG and the type is the system sans stack, matching the app.

## Develop

```sh
cd landing
pnpm install --ignore-workspace   # isolated from the monorepo workspace
pnpm dev                          # http://localhost:4321
pnpm build                        # static output in dist/
pnpm preview                      # serve the built site
```

## Content

Everything lives in `src/pages/index.astro` (sections) with `src/components/` (Wordmark, Icon)
and `src/layouts/Layout.astro` (head/OG). Product copy, feature list, integrations and pricing
are plain arrays at the top of `index.astro` — edit there. App/GitHub URLs are the `APP` and
`GITHUB` constants in the same file.

## Deploy

Static `dist/` — host anywhere (or add to the Caddy stack as its own site). The app itself lives
at `app.stepped.ai`; this is the marketing front door for the apex.
