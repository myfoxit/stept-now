import { describe, expect, it } from 'vitest'

import { htmlToMarkdown, markdownToHtml } from './markdown-bridge'

/** md → html → md, the invariant the editor depends on. */
const roundTrip = (markdown: string) => htmlToMarkdown(markdownToHtml(markdown))

describe('markdownToHtml', () => {
  it('maps every constrained node type to TipTap-parseable HTML', () => {
    expect(markdownToHtml('# One')).toBe('<h1>One</h1>')
    expect(markdownToHtml('#### Four')).toBe('<h4>Four</h4>')
    expect(markdownToHtml('Plain text')).toBe('<p>Plain text</p>')
    expect(markdownToHtml('**b** and *i* and `c`')).toBe(
      '<p><strong>b</strong> and <em>i</em> and <code>c</code></p>'
    )
    expect(markdownToHtml('---')).toBe('<hr>')
    expect(markdownToHtml('> quoted')).toBe('<blockquote><p>quoted</p></blockquote>')
    expect(markdownToHtml('- a\n- b')).toBe(
      '<ul><li><p>a</p></li><li><p>b</p></li></ul>'
    )
    expect(markdownToHtml('1. a\n2. b')).toBe(
      '<ol><li><p>a</p></li><li><p>b</p></li></ol>'
    )
    expect(markdownToHtml('```js\nconst x = 1\n```')).toBe(
      '<pre><code class="language-js">const x = 1</code></pre>'
    )
    expect(markdownToHtml('[docs](https://stept.dev)')).toBe(
      '<p><a href="https://stept.dev">docs</a></p>'
    )
    expect(markdownToHtml('![Logo](https://cdn.test/l.png)')).toBe(
      '<img src="https://cdn.test/l.png" alt="Logo">'
    )
  })

  it('nests lists up to three levels and folds deeper ones back', () => {
    expect(markdownToHtml('- a\n  - b\n    - c')).toBe(
      '<ul><li><p>a</p><ul><li><p>b</p><ul><li><p>c</p></li></ul></li></ul></li></ul>'
    )
    // A fourth level collapses into the third rather than exploding.
    const deep = markdownToHtml('- a\n  - b\n    - c\n      - d')
    expect(deep).toContain('<li><p>c</p></li><li><p>d</p></li>')
  })

  it('escapes HTML and rejects javascript: URLs in links and images', () => {
    expect(markdownToHtml('<img src=x onerror=alert(1)>')).toBe(
      '<p>&lt;img src=x onerror=alert(1)&gt;</p>'
    )
    const evilLink = markdownToHtml('[x](javascript:alert(1))')
    expect(evilLink).not.toContain('href')
    expect(evilLink).toContain('x')

    const evilImage = markdownToHtml('![evil](javascript:alert(1))')
    expect(evilImage).not.toContain('<img')
    expect(evilImage).toContain('evil')

    expect(markdownToHtml('![evil](data:image/svg+xml;base64,PHN2Zz4=)')).toBe('<p>evil</p>')
    expect(markdownToHtml('[x](vbscript:msgbox)')).toBe('<p>x</p>')
  })

  it('keeps same-origin upload paths usable as image sources', () => {
    expect(markdownToHtml('![shot](/api/v1/w/w1/files/abc.png)')).toBe(
      '<img src="/api/v1/w/w1/files/abc.png" alt="shot">'
    )
  })
})

describe('htmlToMarkdown', () => {
  it('serialises the HTML TipTap actually produces', () => {
    expect(htmlToMarkdown('<h2>Title</h2><p>Body</p>')).toBe('## Title\n\nBody')
    expect(htmlToMarkdown('<ul><li><p>a</p></li></ul>')).toBe('- a')
    expect(htmlToMarkdown('<li>bare</li>')).toBe('bare')
    expect(htmlToMarkdown('<p></p>')).toBe('')
    expect(htmlToMarkdown('')).toBe('')
    expect(htmlToMarkdown('<p>a<br>b</p>')).toBe('a\nb')
    expect(htmlToMarkdown('<h5>deep</h5>')).toBe('#### deep')
  })

  it('drops unsafe hrefs and image sources', () => {
    expect(htmlToMarkdown('<p><a href="javascript:alert(1)">x</a></p>')).toBe('x')
    expect(htmlToMarkdown('<img src="javascript:alert(1)" alt="evil">')).toBe('')
  })

  it('escapes text that would otherwise re-parse as markup', () => {
    expect(htmlToMarkdown('<p>2 * 3 * 4</p>')).toBe('2 \\* 3 \\* 4')
    expect(htmlToMarkdown('<p># not a heading</p>')).toBe('\\# not a heading')
    expect(htmlToMarkdown('<p>- not a list</p>')).toBe('\\- not a list')
  })
})

describe('round trip stability', () => {
  const cases: Record<string, string> = {
    heading1: '# Heading one',
    heading4: '#### Heading four',
    paragraph: 'Just a paragraph of text.',
    bold: 'Some **bold** words.',
    italic: 'Some *italic* words.',
    inlineCode: 'Run `npm install` first.',
    mixedMarks: '**bold** then *italic* then `code`.',
    boldInsideItalic: 'a **bold `code`** tail',
    link: 'Read the [docs](https://stept.dev/docs) now.',
    mailtoLink: 'Mail [us](mailto:hi@stept.dev).',
    relativeLink: 'Go [home](/knowledge).',
    image: '![Architecture](https://cdn.test/arch.png)',
    imageNoAlt: '![](https://cdn.test/arch.png)',
    uploadedImage: '![Shot](/api/v1/w/w1/files/abc.png)',
    horizontalRule: '---',
    blockquote: '> A quoted line.',
    codeFence: '```\nconst x = 1\n```',
    codeFenceLang: '```python\nprint("hi")\n```',
    bulletList: '- one\n- two\n- three',
    orderedList: '1. one\n2. two\n3. three',
    nestedBullets: '- one\n  - one a\n  - one b\n- two',
    tripleNested: '- one\n  - two\n    - three',
    mixedNesting: '- bullet\n  1. numbered\n  2. more',
    softBreak: 'line one\nline two',
    multiBlock: '# Title\n\nIntro paragraph.\n\n- a\n- b\n\n> note\n\n---\n\nEnd.',
    escapedStar: 'literal \\* star',
    escapedBracket: 'literal \\[bracket\\]',
    escapedHeading: '\\# not a heading',
    quotedList: '> - a\n> - b',
  }

  for (const [name, markdown] of Object.entries(cases)) {
    it(`round-trips ${name}`, () => {
      expect(roundTrip(markdown)).toBe(markdown)
      // …and is idempotent on a second pass.
      expect(roundTrip(roundTrip(markdown))).toBe(markdown)
    })
  }
})
