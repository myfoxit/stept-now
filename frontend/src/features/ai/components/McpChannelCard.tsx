/**
 * Agent builder card for the MCP channel: expose this agent at
 * /mcp/agents/{id} to Claude Code/Desktop, Cursor or ChatGPT. Owns
 * settings.mcp (enabled + approval_mode, other settings keys preserved),
 * shows the tool exposure preview and mints agent-bound API keys with the
 * raw key dropped straight into the install snippets.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plug, Plus, Trash2, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import {
  MCP_CLIENTS,
  McpSnippets,
  SnippetBlock,
  mcpOrigin,
} from '@/features/settings/components/McpSnippets'
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '@/features/settings/hooks'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import {
  aiApi,
  aiKeys,
  type Agent,
  type McpApprovalMode,
  type McpChannelSettings,
  type ToolConfig,
} from '../api'
import { useActions } from '../hooks'

const APPROVAL_MODES: { value: McpApprovalMode; label: string; help: string }[] = [
  {
    value: 'ask_in_chat',
    label: 'Ask in chat — client prompts the user (recommended)',
    help: 'Write tools carry destructive/confirm hints so Claude Code, Claude Desktop and Cursor prompt the user before sending. The server runs whatever the client sends.',
  },
  {
    value: 'ask_in_stept',
    label: 'Ask in Stept — approve in the dashboard',
    help: 'Write calls pause and return an approval link; a teammate approves them under AI → Approvals and the client retries. A back-stop for clients that ignore tool hints.',
  },
  {
    value: 'never_ask',
    label: 'Never ask — auto-approve writes',
    help: 'No prompts. The MCP client will not warn the user before writes. Only use with trusted automations.',
  },
  {
    value: 'deny',
    label: 'Deny — block all writes over MCP',
    help: 'Every write tool call is rejected. Reads still work.',
  },
]

/** Read-tool heuristic shared with the backend agent endpoint. */
const READ_PREFIXES = [
  'search_',
  'get_',
  'list_',
  'ask_',
  'describe_',
  'fetch_',
  'read_',
  'find_',
  'lookup_',
  'show_',
]

function isReadName(name: string): boolean {
  return READ_PREFIXES.some((prefix) => name.startsWith(prefix))
}

/** Builtin tools the agent MCP endpoint exposes (page_* client tools never are). */
const MCP_BUILTINS = new Set(['search_knowledge', 'find_guide'])

/** Mirrors `_slugify` in backend/app/mcp/agent_endpoint.py — the preview must
 * show the tool names the endpoint actually exposes, including how it treats
 * underscores already in the name. */
function slugify(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'action'
  )
}

/**
 * Preview of the tools an external MCP client will see for this agent:
 * ask_agent always, exposed builtins, and custom actions as action_<slug>
 * classified read/write by the shared prefix heuristic.
 */
export function deriveMcpExposure(
  tools: ToolConfig[],
  actions: { id: string; name: string }[]
): { reads: string[]; writes: string[] } {
  const reads = ['ask_agent']
  const writes: string[] = []
  for (const tool of tools) {
    if (tool.policy === 'disabled') continue
    if (tool.key.startsWith('page_')) continue
    if (tool.key.startsWith('action:')) {
      const action = actions.find((a) => a.id === tool.key.slice('action:'.length))
      if (!action) continue
      const slug = slugify(action.name)
      ;(isReadName(slug) ? reads : writes).push(`action_${slug}`)
    } else if (MCP_BUILTINS.has(tool.key)) {
      ;(isReadName(tool.key) ? reads : writes).push(tool.key)
    }
  }
  return { reads, writes }
}

export function McpChannelCard({ agent }: { agent: Agent }) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const canManage = useHasPerm('ai:manage')
  const canManageKeys = useHasPerm('apikeys:manage')
  const actions = useActions()
  const keys = useApiKeys()
  const createKey = useCreateApiKey()
  const revokeKey = useRevokeApiKey()

  const [client, setClient] = useState(MCP_CLIENTS[0].value)
  const [rawKey, setRawKey] = useState<string | null>(null)

  const settings = (agent.settings ?? {}) as Record<string, unknown>
  const mcp: McpChannelSettings = (settings.mcp as McpChannelSettings | undefined) ?? {
    enabled: false,
    approval_mode: 'ask_in_chat',
  }
  const mode = APPROVAL_MODES.find((m) => m.value === mcp.approval_mode) ?? APPROVAL_MODES[0]
  const endpoint = `${mcpOrigin()}/mcp/agents/${agent.id}`

  const exposure = useMemo(
    () => deriveMcpExposure((agent.tools as ToolConfig[]) ?? [], actions.data ?? []),
    [agent.tools, actions.data]
  )

  const agentKeys = (keys.data ?? []).filter((k) => k.agent_id === agent.id && !k.revoked_at)

  /** PATCH settings.mcp while preserving every sibling settings key. */
  const updateMcp = useMutation({
    mutationFn: (next: McpChannelSettings) =>
      aiApi.updateAgent(agent.id, { settings: { ...settings, mcp: next } }),
    onMutate: async (next) => {
      const key = aiKeys.agent(workspaceId, agent.id)
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<Agent>(key)
      if (previous) {
        queryClient.setQueryData<Agent>(key, {
          ...previous,
          settings: { ...(previous.settings as Record<string, unknown>), mcp: next },
        })
      }
      return { previous }
    },
    onError: (error, _next, ctx) => {
      if (ctx?.previous) {
        queryClient.setQueryData(aiKeys.agent(workspaceId, agent.id), ctx.previous)
      }
      toast.error(error instanceof ApiError ? error.message : 'Could not update the MCP channel')
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: aiKeys.agent(workspaceId, agent.id) })
      void queryClient.invalidateQueries({ queryKey: aiKeys.agents(workspaceId) })
    },
  })

  async function quickCreateKey() {
    const label = MCP_CLIENTS.find((c) => c.value === client)?.label ?? 'MCP client'
    try {
      const created = await createKey.mutateAsync({
        name: `${label} · ${agent.name}`,
        scopes: ['read', 'write'],
        agent_id: agent.id,
      })
      setRawKey(created.key)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-sm bg-brand/10 text-brand">
            <Plug className="size-3.5" />
          </span>
          <div className="min-w-0">
            <CardTitle className="text-sm">Claude / ChatGPT / Cursor (MCP)</CardTitle>
            <p className="text-xs text-muted-foreground">
              Drive {agent.name || 'this agent'} from an external AI chat on the user&rsquo;s own
              subscription. Calls authenticate with an API key and run through Stept&rsquo;s
              permission and audit pipeline.
            </p>
          </div>
        </div>
        <Switch
          checked={mcp.enabled}
          onCheckedChange={(enabled) => updateMcp.mutate({ ...mcp, enabled })}
          aria-label="Enable MCP channel"
          disabled={!canManage}
        />
      </CardHeader>

      {mcp.enabled ? (
        <CardContent className="grid gap-5">
          {/* Endpoint */}
          <div className="grid gap-1.5">
            <Label>Endpoint URL</Label>
            <SnippetBlock value={endpoint} copyLabel="Copy agent MCP endpoint" />
          </div>

          {/* Approval mode */}
          <div className="grid gap-1.5">
            <Label htmlFor="mcp-approval-mode">Approval mode for write tools</Label>
            <NativeSelect
              id="mcp-approval-mode"
              value={mcp.approval_mode}
              onChange={(e) =>
                updateMcp.mutate({ ...mcp, approval_mode: e.target.value as McpApprovalMode })
              }
              disabled={!canManage}
              className="w-full"
            >
              {APPROVAL_MODES.map((m) => (
                <NativeSelectOption key={m.value} value={m.value}>
                  {m.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">{mode.help}</p>
          </div>

          {/* Tool exposure preview */}
          <div className="grid gap-1.5">
            <Label>Tools the external client will see</Label>
            <div className="grid grid-cols-2 gap-3 rounded-md border p-3">
              <div className="grid content-start gap-1">
                <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Reads (auto-allowed)
                </span>
                {exposure.reads.map((tool) => (
                  <span key={tool} className="font-mono text-[11px]">
                    {tool}
                  </span>
                ))}
              </div>
              <div className="grid content-start gap-1">
                <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Writes (require approval)
                </span>
                {exposure.writes.length === 0 ? (
                  <span className="text-[11px] text-muted-foreground">none</span>
                ) : (
                  exposure.writes.map((tool) => (
                    <span key={tool} className="font-mono text-[11px]">
                      {tool}
                    </span>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Install + agent-bound keys */}
          <div className="grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label>Install in your client</Label>
              {canManageKeys ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={quickCreateKey}
                  disabled={createKey.isPending}
                >
                  <Plus className="size-4" />
                  {createKey.isPending ? 'Creating…' : 'Create key for this client'}
                </Button>
              ) : null}
            </div>

            <McpSnippets
              endpointUrl={endpoint}
              rawKey={rawKey ?? undefined}
              value={client}
              onValueChange={setClient}
            />

            {rawKey ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
                <p className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                  <TriangleAlert className="size-4 shrink-0" />
                  This key is shown once — copy it now. The snippets above already contain it.
                </p>
                <Button size="sm" variant="outline" onClick={() => setRawKey(null)}>
                  Done
                </Button>
              </div>
            ) : null}

            {agentKeys.length > 0 ? (
              <div className="grid gap-1.5">
                <span className="text-xs text-muted-foreground">
                  Keys bound to this agent (valid only on this endpoint)
                </span>
                {agentKeys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center gap-2 rounded-md border px-2.5 py-1.5"
                  >
                    <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-xs">{key.name}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {key.prefix}…
                    </span>
                    <Badge variant="outline" className="text-[10px] text-muted-foreground">
                      agent-bound
                    </Badge>
                    {canManageKeys ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6 text-muted-foreground hover:text-destructive"
                        aria-label={`Revoke ${key.name}`}
                        disabled={revokeKey.isPending}
                        onClick={() => revokeKey.mutate(key.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </CardContent>
      ) : null}
    </Card>
  )
}
