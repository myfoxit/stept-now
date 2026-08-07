/**
 * Shared MCP install snippets: per-client tabs with copyable setup blocks.
 * Used by Settings → MCP (workspace endpoint /mcp) and the agent builder's
 * MCP channel card (per-agent endpoint /mcp/agents/{id}).
 * Snippet templates mirror docs/MCP-CONTRACTS.md exactly.
 */

import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

/** Placeholder baked into snippets until a real key is minted. */
export const KEY_PLACEHOLDER = 'YOUR_STEPT_KEY'

export interface McpClientDef {
  value: string
  label: string
  hint: string
}

export const MCP_CLIENTS: McpClientDef[] = [
  { value: 'claude-code', label: 'Claude Code', hint: 'Run in your terminal.' },
  {
    value: 'claude-desktop',
    label: 'Claude Desktop',
    hint: 'Add to claude_desktop_config.json under mcpServers.',
  },
  { value: 'cursor', label: 'Cursor', hint: 'Add to .cursor/mcp.json (or Cursor Settings → MCP).' },
  {
    value: 'chatgpt',
    label: 'ChatGPT',
    hint: 'In ChatGPT settings → Connectors → Add connector. Use these values:',
  },
  { value: 'curl', label: 'curl', hint: 'Smoke-test the endpoint from your shell.' },
]

/**
 * Backend origin for MCP endpoints. The SPA can be served from a different
 * origin than the API (vite on :5273 vs backend on :8600 in dev, and the dev
 * proxy does not forward /mcp), so prefer the configured API base and fall
 * back to the page origin for single-origin production deploys.
 */
export function mcpOrigin(): string {
  const base = import.meta.env.VITE_API_BASE_URL
  if (base) {
    try {
      return new URL(base, window.location.origin).origin
    } catch {
      /* malformed env — fall through to defaults */
    }
  }
  if (import.meta.env.DEV) return 'http://localhost:8600'
  return window.location.origin
}

function mcpServersJson(url: string, key: string): string {
  return JSON.stringify(
    { mcpServers: { stept: { url, headers: { Authorization: `Bearer ${key}` } } } },
    null,
    2
  )
}

/** Exact install snippet for a client tab; falls back to the key placeholder. */
export function mcpSnippet(client: string, url: string, rawKey?: string): string {
  const key = rawKey || KEY_PLACEHOLDER
  switch (client) {
    case 'claude-code':
      return `claude mcp add --transport http stept ${url} --header "Authorization: Bearer ${key}"`
    case 'claude-desktop':
    case 'cursor':
      return mcpServersJson(url, key)
    case 'chatgpt':
      return `URL: ${url}\nAuthorization: Bearer ${key}`
    case 'curl':
      return `curl -X POST ${url} -H 'Authorization: Bearer ${key}' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`
    default:
      return url
  }
}

/** Monospace block with a copy button (Copy → Check swap + toast). */
export function SnippetBlock({
  value,
  copyLabel = 'Copy snippet',
}: {
  value: string
  copyLabel?: string
}) {
  const [copied, setCopied] = useState(false)

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      toast.success('Copied')
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy')
    }
  }

  return (
    <div className="relative">
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-md border bg-muted/40 p-2 pr-9 font-mono text-[11px] leading-relaxed">
        {value}
      </pre>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-1 top-1 size-6"
        aria-label={copyLabel}
        onClick={onCopy}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </Button>
    </div>
  )
}

/**
 * Client tabs + snippet blocks. Uncontrolled by default; pass value/onValueChange
 * when the parent needs the selected client (e.g. one-click key naming).
 */
export function McpSnippets({
  endpointUrl,
  rawKey,
  value,
  onValueChange,
}: {
  endpointUrl: string
  rawKey?: string
  value?: string
  onValueChange?: (client: string) => void
}) {
  const control =
    value === undefined ? { defaultValue: MCP_CLIENTS[0].value } : { value, onValueChange }
  return (
    <Tabs {...control} className="gap-2">
      <TabsList className="h-8 max-w-full overflow-x-auto">
        {MCP_CLIENTS.map((client) => (
          <TabsTrigger key={client.value} value={client.value} className="px-2 text-xs">
            {client.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {MCP_CLIENTS.map((client) => (
        <TabsContent key={client.value} value={client.value} className="mt-1 space-y-1.5">
          <p className="text-xs text-muted-foreground">{client.hint}</p>
          <SnippetBlock
            value={mcpSnippet(client.value, endpointUrl, rawKey)}
            copyLabel={`Copy ${client.label} snippet`}
          />
        </TabsContent>
      ))}
    </Tabs>
  )
}
