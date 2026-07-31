/**
 * Minimal, dependency-free markdown renderer. Supports headings, bold/italic,
 * inline code, fenced code blocks, unordered/ordered lists, blockquotes and
 * links. Renders through React text nodes (never dangerouslySetInnerHTML), so
 * user content cannot inject markup.
 */

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** Inline formatting: `code`, **bold**, *italic*, [text](url). */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g
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
    } else {
      const linkMatch = /\[([^\]]+)\]\(([^)]+)\)/.exec(token)
      if (linkMatch) {
        nodes.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline underline-offset-2"
          >
            {linkMatch[1]}
          </a>
        )
      }
    }
    last = match.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

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

  for (const raw of lines) {
    const line = raw

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
