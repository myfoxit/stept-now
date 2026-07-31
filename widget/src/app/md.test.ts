import { describe, expect, it } from 'vitest'

import { escapeHtml, renderMarkdown } from './md'

describe('escapeHtml', () => {
  it('neutralizes HTML control characters', () => {
    expect(escapeHtml('<script>alert(1)</script>')).toBe(
      '&lt;script&gt;alert(1)&lt;/script&gt;',
    )
  })
})

describe('renderMarkdown', () => {
  it('renders bold, headings and paragraphs', () => {
    const html = renderMarkdown('# Title\n\nHello **world**')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<strong>world</strong>')
    expect(html).toContain('<p>Hello <strong>world</strong></p>')
  })

  it('renders safe links but drops javascript: URLs', () => {
    const ok = renderMarkdown('[docs](https://stept.io/docs)')
    expect(ok).toContain('<a href="https://stept.io/docs"')
    expect(ok).toContain('rel="noopener noreferrer"')

    const evil = renderMarkdown('[x](javascript:alert(1))')
    expect(evil).not.toContain('href')
    expect(evil).toContain('x')
  })

  it('escapes embedded HTML so content cannot inject markup', () => {
    const html = renderMarkdown('watch <img src=x onerror=alert(1)>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('builds unordered lists', () => {
    const html = renderMarkdown('- one\n- two')
    expect(html).toContain('<ul><li>one</li><li>two</li></ul>')
  })

  it('renders images with alt text and lazy loading', () => {
    const html = renderMarkdown('![Architecture](https://cdn.stept.io/arch.png)')
    expect(html).toContain('<img src="https://cdn.stept.io/arch.png" alt="Architecture"')
    expect(html).toContain('loading="lazy"')
  })

  it('drops javascript: and data: image sources but keeps the alt text', () => {
    const evil = renderMarkdown('![boom](javascript:alert(1))')
    expect(evil).not.toContain('<img')
    expect(evil).toContain('boom')

    const data = renderMarkdown('![boom](data:image/svg+xml;base64,PHN2Zz4=)')
    expect(data).not.toContain('<img')
    expect(data).toContain('boom')

    // A bare word is not a path — still dropped.
    const bare = renderMarkdown('![boom](arch.png)')
    expect(bare).not.toContain('<img')
    expect(bare).toContain('boom')
  })

  it('keeps root-relative image sources so the player can absolutize them', () => {
    // Editor uploads are stored as /api/widget/media/{ws}/{key}; the tour player
    // rewrites them against its apiBase, which needs the <img> to survive here.
    const html = renderMarkdown('![Shot](/api/widget/media/ws_1/2026/07/abc-shot.png)')
    expect(html).toContain('<img src="/api/widget/media/ws_1/2026/07/abc-shot.png" alt="Shot"')
  })

  it('renders images inside a link label without breaking the link', () => {
    const html = renderMarkdown('[docs](https://stept.io/docs) and ![x](https://cdn.stept.io/x.png)')
    expect(html).toContain('<a href="https://stept.io/docs"')
    expect(html).toContain('<img src="https://cdn.stept.io/x.png"')
  })

  it('renders GFM pipe tables', () => {
    const html = renderMarkdown('| Plan | Price |\n| --- | --- |\n| Free | $0 |\n| Pro | $20 |')
    expect(html).toContain('<table>')
    expect(html).toContain('<th>Plan</th><th>Price</th>')
    expect(html).toContain('<tr><td>Free</td><td>$0</td></tr>')
    expect(html).toContain('<tr><td>Pro</td><td>$20</td></tr>')
  })

  it('accepts alignment colons in the table divider and formats cells', () => {
    const html = renderMarkdown('| A | B |\n|:--|--:|\n| **bold** | `code` |')
    expect(html).toContain('<td><strong>bold</strong></td>')
    expect(html).toContain('<td><code>code</code></td>')
  })

  it('keeps a lone horizontal rule an <hr />, not a table', () => {
    expect(renderMarkdown('---')).toContain('<hr />')
  })
})
