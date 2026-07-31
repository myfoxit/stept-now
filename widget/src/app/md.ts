/**
 * Minimal, dependency-free Markdown → HTML renderer.
 *
 * Deliberately small (headings, bold/italic, code, links, lists, blockquotes,
 * hr, paragraphs). Input is HTML-escaped BEFORE any transform, and link targets
 * are restricted to http/https/mailto, so agent/article content can't inject
 * markup or javascript: URLs. Output is a trusted HTML string for
 * `dangerouslySetInnerHTML`.
 */

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function safeUrl(url: string): string | null {
  const trimmed = url.trim()
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed
  if (/^\//.test(trimmed) || /^[\w./#?=&%-]+$/.test(trimmed)) return trimmed
  return null
}

/** Inline transforms applied to already-escaped text. */
function inline(text: string): string {
  let out = text
  // Inline code first so its contents aren't further transformed.
  out = out.replace(/`([^`]+)`/g, (_m, code: string) => `<code>${code}</code>`)
  // Links [label](url)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label: string, url: string) => {
    const href = safeUrl(url)
    return href
      ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : label
  })
  // Autolink bare URLs (avoid ones already inside an href="...").
  out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_m, pre: string, url: string) => {
    const href = safeUrl(url)
    return href ? `${pre}<a href="${href}" target="_blank" rel="noopener noreferrer">${url}</a>` : `${pre}${url}`
  })
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  return out
}

export function renderMarkdown(source: string): string {
  const escaped = escapeHtml(source ?? '')
  const lines = escaped.split(/\r?\n/)
  const html: string[] = []
  let i = 0
  let paragraph: string[] = []
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null

  const flushParagraph = (): void => {
    if (paragraph.length) {
      html.push(`<p>${inline(paragraph.join(' '))}</p>`)
      paragraph = []
    }
  }
  const flushList = (): void => {
    if (list) {
      const items = list.items.map((it) => `<li>${inline(it)}</li>`).join('')
      html.push(`<${list.type}>${items}</${list.type}>`)
      list = null
    }
  }
  const flush = (): void => {
    flushParagraph()
    flushList()
  }

  while (i < lines.length) {
    const line = lines[i]!
    const trimmed = line.trim()

    if (trimmed.startsWith('```')) {
      flush()
      const code: string[] = []
      i++
      while (i < lines.length && !lines[i]!.trim().startsWith('```')) {
        code.push(lines[i]!)
        i++
      }
      i++ // skip closing fence
      html.push(`<pre><code>${code.join('\n')}</code></pre>`)
      continue
    }

    if (trimmed === '') {
      flush()
      i++
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (heading) {
      flush()
      const level = heading[1]!.length
      html.push(`<h${level}>${inline(heading[2]!)}</h${level}>`)
      i++
      continue
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flush()
      html.push('<hr />')
      i++
      continue
    }

    if (trimmed.startsWith('&gt;')) {
      flush()
      html.push(`<blockquote>${inline(trimmed.replace(/^&gt;\s?/, ''))}</blockquote>`)
      i++
      continue
    }

    const ordered = /^\d+\.\s+(.*)$/.exec(trimmed)
    const unordered = /^[-*]\s+(.*)$/.exec(trimmed)
    if (ordered || unordered) {
      flushParagraph()
      const type = ordered ? 'ol' : 'ul'
      const content = (ordered ?? unordered)![1]!
      if (!list || list.type !== type) {
        flushList()
        list = { type, items: [] }
      }
      list.items.push(content)
      i++
      continue
    }

    flushList()
    paragraph.push(trimmed)
    i++
  }

  flush()
  return html.join('\n')
}
