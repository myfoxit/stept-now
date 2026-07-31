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
})
