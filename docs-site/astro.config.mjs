import { defineConfig } from 'astro/config'
import starlight from '@astrojs/starlight'

// Documentation site, served at docs.stepped.ai. Isolated from the pnpm
// workspace like landing/ (own lockfile, pnpm install --ignore-workspace).
export default defineConfig({
  site: 'https://docs.stepped.ai',
  vite: {
    // Bundle Astro's runtime deps into the server chunks instead of leaving
    // them as bare imports. With pnpm they are not resolvable from dist/ at
    // generate time; a stray ancestor node_modules (e.g. ~/node_modules with
    // an ancient clsx) can otherwise shadow them with broken versions.
    ssr: { noExternal: ['clsx', 'html-escaper'] },
  },
  integrations: [
    starlight({
      title: 'Stept',
      description:
        'Documentation for Stept — the open-source Intercom + Fin alternative: shared inbox, AI agent, knowledge base, product tours.',
      // Same mark, palette and type as stepped.ai — see src/styles/stept.css.
      logo: { src: './public/logo.svg', alt: 'Stept' },
      customCss: ['./src/styles/stept.css'],
      favicon: '/favicon.svg',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/myfoxit/stept-now' }],
      components: {
        // The docs are one surface of the product, not an island: link back to
        // the site and the app from the header.
        SocialIcons: './src/components/HeaderLinks.astro',
      },
      editLink: {
        baseUrl: 'https://github.com/myfoxit/stept-now/edit/master/docs-site/',
      },
      sidebar: [
        {
          label: 'Getting started',
          items: [
            { label: 'What is Stept?', slug: 'index' },
            { label: 'Quickstart (cloud)', slug: 'getting-started/quickstart' },
            { label: 'Self-hosting', slug: 'getting-started/self-hosting' },
          ],
        },
        {
          label: 'Product',
          items: [
            { label: 'Inbox & conversations', slug: 'product/inbox' },
            { label: 'Chat widget', slug: 'product/widget' },
            { label: 'Knowledge base & crawling', slug: 'product/knowledge' },
            { label: 'AI agent', slug: 'product/ai-agent' },
            { label: 'Product tours', slug: 'product/tours' },
            { label: 'Help center', slug: 'product/help-center' },
          ],
        },
        {
          label: 'Integrations',
          items: [
            { label: 'Overview', slug: 'integrations/overview' },
            { label: 'MCP (AI clients)', slug: 'integrations/mcp' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Configuration', slug: 'reference/configuration' },
            { label: 'REST API', slug: 'reference/api' },
          ],
        },
      ],
    }),
  ],
})
