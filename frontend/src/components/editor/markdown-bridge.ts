/**
 * Markdown ⇄ HTML bridge for the rich text editor.
 *
 * Markdown stays the persisted format everywhere (article bodies, knowledge
 * documents, tour step bodies) — TipTap is only an editing surface, so every
 * value crossing the component boundary is markdown and every value crossing
 * the TipTap boundary is HTML.
 *
 * The installed TipTap (3.29) ships markdown *spec* helpers for extension
 * authors but no runtime markdown parser/serializer (`Editor.getMarkdown()`
 * does not exist and `EditorOptions.content` is HTML | JSON only), so this
 * bridge is the source of truth.
 *
 * Supported (the "constrained schema"): h1–h4, paragraph, bold, italic, inline
 * code, fenced code, bullet/ordered lists nested up to 3 deep, blockquote,
 * link, image, hard break and horizontal rule. `md → html → md` is stable for
 * everything in that set; markdown outside it degrades to text.
 */

/** Lists deeper than this are folded back into the deepest allowed level. */
export const MAX_LIST_DEPTH = 3

/** Characters that would otherwise be read back as inline markdown syntax. */
const INLINE_ESCAPE = /[\\`*[\]]/g

/** A paragraph starting with one of these needs a leading backslash. */
const BLOCK_MARKER = /^(#{1,6}\s|>|[-+]\s|[-+]$|\d+[.)]\s|-{3,}$)/

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * Link targets we are willing to import into the editor. Anything exotic
 * (`javascript:`, `data:`, `vbscript:`) is dropped rather than sanitised.
 */
function safeLinkUrl(url: string): string | null {
  const trimmed = url.trim()
  if (!trimmed) return null
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed
  if (/^[#/]/.test(trimmed)) return trimmed
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return null
  return trimmed
}

/** Images are stricter: remote http(s) or a same-origin path (our /files URLs). */
function safeImageUrl(url: string): string | null {
  const trimmed = url.trim()
  if (!trimmed) return null
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (/^\//.test(trimmed)) return trimmed
  return null
}

// --- markdown → html --------------------------------------------------------

interface ListNode {
  ordered: boolean
  items: ItemNode[]
}

interface ItemNode {
  text: string
  children: ListNode[]
}

interface RawItem {
  indent: number
  ordered: boolean
  text: string
}

const HEADING_RE = /^(#{1,6})\s+(.*)$/
const FENCE_RE = /^\s*```\s*([\w+-]*)\s*$/
const RULE_RE = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/
const QUOTE_RE = /^\s*>\s?(.*)$/
const ITEM_RE = /^(\s*)(?:[-*+]|(\d+)[.)])\s+(.*)$/
const IMAGE_LINE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/

/** Inline markdown → HTML. Recursive for the contents of bold/italic runs. */
function inlineToHtml(text: string): string {
  let out = ''
  let i = 0
  while (i < text.length) {
    const rest = text.slice(i)
    const ch = text[i]

    if (ch === '\\' && i + 1 < text.length) {
      out += escapeHtml(text[i + 1])
      i += 2
      continue
    }

    if (ch === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i + 1) {
        out += `<code>${escapeHtml(text.slice(i + 1, end))}</code>`
        i = end + 1
        continue
      }
    }

    if (ch === '!' && text[i + 1] === '[') {
      const match = /^!\[([^\]]*)\]\(([^)\s]*)\)/.exec(rest)
      if (match) {
        const src = safeImageUrl(match[2])
        out += src
          ? `<img src="${escapeHtml(src)}" alt="${escapeHtml(match[1])}">`
          : escapeHtml(match[1])
        i += match[0].length
        continue
      }
    }

    if (ch === '[') {
      const match = /^\[([^\]]*)\]\(([^)\s]*)\)/.exec(rest)
      if (match) {
        const href = safeLinkUrl(match[2])
        const label = inlineToHtml(match[1])
        out += href ? `<a href="${escapeHtml(href)}">${label}</a>` : label
        i += match[0].length
        continue
      }
    }

    if (ch === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2)
      if (end > i + 2) {
        out += `<strong>${inlineToHtml(text.slice(i + 2, end))}</strong>`
        i = end + 2
        continue
      }
    }

    if (ch === '*') {
      const match = /^\*([^*\n]+)\*/.exec(rest)
      if (match) {
        out += `<em>${inlineToHtml(match[1])}</em>`
        i += match[0].length
        continue
      }
    }

    out += escapeHtml(ch)
    i += 1
  }
  return out
}

/** Group flat list lines into a nested tree, clamped to MAX_LIST_DEPTH. */
function buildLists(raw: RawItem[]): ListNode[] {
  const roots: ListNode[] = []
  const stack: { indent: number; list: ListNode }[] = []

  const startList = (item: RawItem) => {
    const node: ListNode = { ordered: item.ordered, items: [] }
    const parent = stack[stack.length - 1]
    const parentItem = parent?.list.items[parent.list.items.length - 1]
    if (parentItem) parentItem.children.push(node)
    else roots.push(node)
    stack.push({ indent: item.indent, list: node })
  }

  for (const item of raw) {
    while (stack.length > 1 && item.indent < stack[stack.length - 1].indent) stack.pop()

    let top = stack[stack.length - 1]
    if (top && item.indent > top.indent && stack.length < MAX_LIST_DEPTH) {
      if (top.list.items.length > 0) {
        startList(item)
        top = stack[stack.length - 1]
      }
    }

    if (!top) {
      startList(item)
      top = stack[stack.length - 1]
    } else if (top.list.ordered !== item.ordered) {
      // A different marker at the same level starts a sibling list.
      stack.pop()
      startList(item)
      top = stack[stack.length - 1]
    }

    top.list.items.push({ text: item.text, children: [] })
  }
  return roots
}

function listToHtml(node: ListNode): string {
  const tag = node.ordered ? 'ol' : 'ul'
  const items = node.items
    .map(
      (item) =>
        `<li><p>${inlineToHtml(item.text)}</p>${item.children.map(listToHtml).join('')}</li>`
    )
    .join('')
  return `<${tag}>${items}</${tag}>`
}

function blocksToHtml(lines: string[], depth: number): string {
  const html: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (trimmed === '') {
      i += 1
      continue
    }

    const fence = FENCE_RE.exec(line)
    if (fence) {
      const code: string[] = []
      i += 1
      while (i < lines.length && !FENCE_RE.test(lines[i])) {
        code.push(lines[i])
        i += 1
      }
      i += 1 // closing fence
      const language = fence[1] ? ` class="language-${escapeHtml(fence[1])}"` : ''
      html.push(`<pre><code${language}>${escapeHtml(code.join('\n'))}</code></pre>`)
      continue
    }

    if (RULE_RE.test(line)) {
      html.push('<hr>')
      i += 1
      continue
    }

    const heading = HEADING_RE.exec(trimmed)
    if (heading) {
      const level = Math.min(heading[1].length, 4)
      html.push(`<h${level}>${inlineToHtml(heading[2])}</h${level}>`)
      i += 1
      continue
    }

    const quote = QUOTE_RE.exec(line)
    if (quote) {
      const inner: string[] = []
      while (i < lines.length) {
        const next = QUOTE_RE.exec(lines[i])
        if (!next) break
        inner.push(next[1])
        i += 1
      }
      const body = depth < 4 ? blocksToHtml(inner, depth + 1) : `<p>${inlineToHtml(inner.join(' '))}</p>`
      html.push(`<blockquote>${body || '<p></p>'}</blockquote>`)
      continue
    }

    if (ITEM_RE.test(line)) {
      const raw: RawItem[] = []
      while (i < lines.length) {
        const item = ITEM_RE.exec(lines[i])
        if (!item) break
        raw.push({ indent: item[1].length, ordered: item[2] !== undefined, text: item[3] })
        i += 1
      }
      html.push(buildLists(raw).map(listToHtml).join(''))
      continue
    }

    if (IMAGE_LINE_RE.test(trimmed)) {
      const image = IMAGE_LINE_RE.exec(trimmed)!
      const src = safeImageUrl(image[2])
      html.push(
        src
          ? `<img src="${escapeHtml(src)}" alt="${escapeHtml(image[1])}">`
          : `<p>${escapeHtml(image[1])}</p>`
      )
      i += 1
      continue
    }

    // Paragraph: consecutive plain lines, soft breaks kept as <br>.
    const paragraph: string[] = []
    while (i < lines.length) {
      const next = lines[i]
      if (
        next.trim() === '' ||
        FENCE_RE.test(next) ||
        RULE_RE.test(next) ||
        HEADING_RE.test(next.trim()) ||
        QUOTE_RE.test(next) ||
        ITEM_RE.test(next) ||
        IMAGE_LINE_RE.test(next.trim())
      ) {
        break
      }
      paragraph.push(next.trim())
      i += 1
    }
    html.push(`<p>${paragraph.map(inlineToHtml).join('<br>')}</p>`)
  }

  return html.join('')
}

/** Markdown → the HTML TipTap parses into its document. */
export function markdownToHtml(markdown: string): string {
  if (!markdown) return ''
  return blocksToHtml(markdown.replace(/\r\n?/g, '\n').split('\n'), 0)
}

// --- html → markdown --------------------------------------------------------

function escapeMarkdown(text: string): string {
  return text.replace(INLINE_ESCAPE, (char) => `\\${char}`)
}

function attr(el: Element, name: string): string {
  return el.getAttribute(name) ?? ''
}

/** Inline serialisation of an element's children. */
function inlineToMarkdown(node: Node): string {
  let out = ''
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === 3) {
      out += escapeMarkdown((child.textContent ?? '').replace(/\r?\n\s*/g, ' '))
      continue
    }
    if (child.nodeType !== 1) continue
    const el = child as Element
    switch (el.tagName) {
      case 'BR':
        out += '\n'
        break
      case 'STRONG':
      case 'B': {
        const inner = inlineToMarkdown(el)
        out += inner ? `**${inner}**` : ''
        break
      }
      case 'EM':
      case 'I': {
        const inner = inlineToMarkdown(el)
        out += inner ? `*${inner}*` : ''
        break
      }
      case 'CODE':
        out += `\`${(el.textContent ?? '').replace(/`/g, '')}\``
        break
      case 'A': {
        const href = safeLinkUrl(attr(el, 'href'))
        const label = inlineToMarkdown(el)
        out += href ? `[${label}](${href})` : label
        break
      }
      case 'IMG': {
        const src = safeImageUrl(attr(el, 'src'))
        out += src ? `![${escapeMarkdown(attr(el, 'alt'))}](${src})` : ''
        break
      }
      default:
        out += inlineToMarkdown(el)
    }
  }
  return out
}

function listItemText(li: Element): string {
  let out = ''
  for (const child of Array.from(li.childNodes)) {
    if (child.nodeType === 1 && ['UL', 'OL'].includes((child as Element).tagName)) continue
    const piece =
      child.nodeType === 3
        ? escapeMarkdown((child.textContent ?? '').replace(/\r?\n\s*/g, ' '))
        : inlineToMarkdown(child)
    if (!piece) continue
    out += out ? ` ${piece}` : piece
  }
  return out.trim()
}

function listToMarkdown(el: Element, level: number): string {
  const ordered = el.tagName === 'OL'
  const indent = '  '.repeat(Math.min(level, MAX_LIST_DEPTH - 1))
  const lines: string[] = []
  let index = 1
  for (const li of Array.from(el.children)) {
    if (li.tagName !== 'LI') continue
    lines.push(`${indent}${ordered ? `${index++}. ` : '- '}${listItemText(li)}`)
    for (const nested of Array.from(li.children)) {
      if (nested.tagName === 'UL' || nested.tagName === 'OL') {
        lines.push(listToMarkdown(nested, level + 1))
      }
    }
  }
  return lines.join('\n')
}

function blocksToMarkdown(parent: Node): string[] {
  const blocks: string[] = []
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === 3) {
      const text = (child.textContent ?? '').trim()
      if (text) blocks.push(escapeMarkdown(text))
      continue
    }
    if (child.nodeType !== 1) continue
    const el = child as Element

    if (/^H[1-6]$/.test(el.tagName)) {
      const level = Math.min(Number(el.tagName.slice(1)), 4)
      blocks.push(`${'#'.repeat(level)} ${inlineToMarkdown(el)}`)
      continue
    }
    if (el.tagName === 'P') {
      const text = inlineToMarkdown(el)
      if (!text.trim()) continue
      blocks.push(BLOCK_MARKER.test(text) ? `\\${text}` : text)
      continue
    }
    if (el.tagName === 'UL' || el.tagName === 'OL') {
      const list = listToMarkdown(el, 0)
      if (list) blocks.push(list)
      continue
    }
    if (el.tagName === 'BLOCKQUOTE') {
      const inner = blocksToMarkdown(el)
      const body = inner.length > 0 ? inner.join('\n\n') : ''
      blocks.push(
        body
          .split('\n')
          .map((line) => (line ? `> ${line}` : '>'))
          .join('\n')
      )
      continue
    }
    if (el.tagName === 'PRE') {
      const code = el.querySelector('code')
      const language = /language-([\w+-]+)/.exec(code?.className ?? '')?.[1] ?? ''
      blocks.push(`\`\`\`${language}\n${(code ?? el).textContent ?? ''}\n\`\`\``)
      continue
    }
    if (el.tagName === 'HR') {
      blocks.push('---')
      continue
    }
    if (el.tagName === 'IMG') {
      const src = safeImageUrl(attr(el, 'src'))
      if (src) blocks.push(`![${escapeMarkdown(attr(el, 'alt'))}](${src})`)
      continue
    }
    // Unknown wrapper (div, section, …): treat as transparent.
    blocks.push(...blocksToMarkdown(el))
  }
  return blocks
}

/** TipTap HTML → the markdown we persist. */
export function htmlToMarkdown(html: string): string {
  if (!html || !html.trim()) return ''
  const parsed = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  return blocksToMarkdown(parsed.body).join('\n\n').trim()
}
