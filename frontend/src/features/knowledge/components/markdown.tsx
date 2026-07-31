/**
 * Minimal, dependency-free markdown renderer. Supports headings, bold/italic,
 * inline code, fenced code blocks, unordered/ordered lists, blockquotes,
 * links, images and GFM pipe tables. Renders through React text nodes (never
 * dangerouslySetInnerHTML), so user content cannot inject markup — image and
 * link targets are additionally restricted to safe schemes.
 */

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** Links: http(s), mailto, anchors and same-origin paths. Nothing else. */
function safeHref(url: string): string | null {
  const trimmed = url.trim()
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed
  if (/^[#/]/.test(trimmed)) return trimmed
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return null
  return trimmed
}

/** Images are stricter: remote http(s) or a same-origin path. */
function safeSrc(url: string): string | null {
  const trimmed = url.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (/^\//.test(trimmed)) return trimmed
  return null
}

/** Inline formatting: `code`, **bold**, *italic*, [text](url), ![alt](src). */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(!\[[^\]]*\]\([^)]+\))|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let match: RegExpExecArray | null
  let i = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyPrefix}-${i++}`
    if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('*')) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>)
    } else if (token.startsWith('![')) {
      const imageMatch = /!\[([^\]]*)\]\(([^)]+)\)/.exec(token)
      const src = imageMatch ? safeSrc(imageMatch[2]) : null
      if (imageMatch && src) {
        nodes.push(
          <img key={key} src={src} alt={imageMatch[1]} className="max-w-full rounded-md" />
        )
      } else if (imageMatch) {
        // Unsafe scheme — keep the alt text, drop the image.
        nodes.push(imageMatch[1])
      }
    } else {
      const linkMatch = /\[([^\]]+)\]\(([^)]+)\)/.exec(token)
      const href = linkMatch ? safeHref(linkMatch[2]) : null
      if (linkMatch && href) {
        nodes.push(
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline underline-offset-2"
          >
            {linkMatch[1]}
          </a>
        )
      } else if (linkMatch) {
        nodes.push(linkMatch[1])
      }
    }
    last = match.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

/** A `| a | b |` row split into trimmed cells. */
function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

const TABLE_ROW = /^\s*\|.*\|\s*$/
const TABLE_DIVIDER = /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/

export function Markdown({ content, className }: { content: string; className?: string }) {
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  let code: string[] | null = null
  let key = 0

  const flushList = () => {
    if (!list) return
    const items = list.items.map((item, i) => <li key={i}>{renderInline(item, `li-${key}-${i}`)}</li>)
    blocks.push(
      list.ordered ? (
        <ol key={key++} className="ml-5 list-decimal space-y-1">
          {items}
        </ol>
      ) : (
        <ul key={key++} className="ml-5 list-disc space-y-1">
          {items}
        </ul>
      )
    )
    list = null
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]

    if (code !== null) {
      if (line.trim().startsWith('```')) {
        blocks.push(
          <pre key={key++} className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
            <code>{code.join('\n')}</code>
          </pre>
        )
        code = null
      } else {
        code.push(line)
      }
      continue
    }
    if (line.trim().startsWith('```')) {
      flushList()
      code = []
      continue
    }

    // GFM pipe table: header row, divider row, then body rows.
    if (TABLE_ROW.test(line) && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
      flushList()
      const header = tableCells(line)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && TABLE_ROW.test(lines[index])) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      index -= 1
      const tableKey = key++
      blocks.push(
        <div key={tableKey} className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b">
                {header.map((cell, i) => (
                  <th key={i} className="px-2 py-1.5 font-medium">
                    {renderInline(cell, `th-${tableKey}-${i}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r} className="border-b last:border-0">
                  {row.map((cell, c) => (
                    <td key={c} className="px-2 py-1.5 align-top text-muted-foreground">
                      {renderInline(cell, `td-${tableKey}-${r}-${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      flushList()
      const level = heading[1].length
      const sizes = ['text-xl font-semibold', 'text-lg font-semibold', 'text-base font-semibold', 'text-sm font-semibold']
      blocks.push(
        <p key={key++} className={cn('mt-1', sizes[level - 1])}>
          {renderInline(heading[2], `h-${key}`)}
        </p>
      )
      continue
    }

    const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
    const ordered = /^\s*\d+\.\s+(.*)$/.exec(line)
    if (bullet) {
      if (!list || list.ordered) {
        flushList()
        list = { ordered: false, items: [] }
      }
      list.items.push(bullet[1])
      continue
    }
    if (ordered) {
      if (!list || !list.ordered) {
        flushList()
        list = { ordered: true, items: [] }
      }
      list.items.push(ordered[1])
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote) {
      flushList()
      blocks.push(
        <blockquote key={key++} className="border-l-2 border-border pl-3 text-muted-foreground">
          {renderInline(quote[1], `q-${key}`)}
        </blockquote>
      )
      continue
    }

    if (line.trim() === '') {
      flushList()
      continue
    }

    flushList()
    blocks.push(
      <p key={key++} className="leading-relaxed">
        {renderInline(line, `p-${key}`)}
      </p>
    )
  }
  flushList()
  if (code !== null) {
    blocks.push(
      <pre key={key++} className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
        <code>{code.join('\n')}</code>
      </pre>
    )
  }

  return <div className={cn('space-y-2 text-sm', className)}>{blocks}</div>
}
