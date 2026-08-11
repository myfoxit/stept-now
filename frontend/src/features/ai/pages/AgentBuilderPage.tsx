import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2, Save } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Slider } from '@/components/ui/slider'
import { Spinner } from '@/components/ui/spinner'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useSources } from '@/features/knowledge/hooks'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { aiApi, aiKeys, type AgentSettings, type ToolConfig, type ToolPolicy } from '../api'
import { CustomActionsSection } from '../components/CustomActionsSection'
import { McpChannelCard } from '../components/McpChannelCard'
import { PageHeader, PageShell } from '../components/shell'
import { TestSandbox } from '../components/TestSandbox'
import { ToolPolicyMatrix, type PolicyRow } from '../components/ToolPolicyMatrix'
import { useActions, useAgent, useModelsFlat } from '../hooks'
import { BUILTIN_TOOLS, DEFAULT_ACTION_POLICY } from '../tools'
import { t } from '@/i18n'

export function Component() {
  const { agentId = '' } = useParams()
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const canManage = useHasPerm('ai:manage')
  const { data: agent, isLoading } = useAgent(agentId)
  const models = useModelsFlat()
  const actions = useActions()
  const sources = useSources()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [avatar, setAvatar] = useState('🤖')
  const [status, setStatus] = useState('draft')
  const [modelRef, setModelRef] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tempEnabled, setTempEnabled] = useState(false)
  const [temperature, setTemperature] = useState(0.7)
  const [retrievalEnabled, setRetrievalEnabled] = useState(true)
  const [retrievalK, setRetrievalK] = useState(6)
  const [sourceIds, setSourceIds] = useState<string[] | null>(null)
  const [maxToolCalls, setMaxToolCalls] = useState(8)
  const [requireCitations, setRequireCitations] = useState(false)
  const [handoffMessage, setHandoffMessage] = useState('')
  const [pageControl, setPageControl] = useState(false)
  const [allowPageActions, setAllowPageActions] = useState(false)
  const [clientActionsEnabled, setClientActionsEnabled] = useState(true)
  const [policies, setPolicies] = useState<Record<string, ToolPolicy>>({})

  // Build the tool matrix rows (builtins + custom actions).
  const toolRows: PolicyRow[] = useMemo(() => {
    const actionRows = (actions.data ?? []).map((a) => ({
      key: `action:${a.id}`,
      label: a.name,
      description: a.description || 'Custom action',
    }))
    return [...BUILTIN_TOOLS.map((t) => ({ key: t.key, label: t.label, description: t.description })), ...actionRows]
  }, [actions.data])

  useEffect(() => {
    if (!agent) return
    setName(agent.name)
    setDescription(agent.description ?? '')
    setAvatar(agent.avatar_emoji || '🤖')
    setStatus(agent.status)
    setModelRef(agent.model_ref ?? '')
    setSystemPrompt(agent.system_prompt)
    setTempEnabled(agent.temperature != null)
    setTemperature(agent.temperature ?? 0.7)
    const settings = agent.settings as unknown as AgentSettings
    setRetrievalEnabled(settings?.retrieval?.enabled ?? true)
    setRetrievalK(settings?.retrieval?.k ?? 6)
    setSourceIds(settings?.retrieval?.source_ids ?? null)
    setMaxToolCalls(settings?.guardrails?.max_tool_calls ?? 8)
    setRequireCitations(settings?.guardrails?.require_citations ?? false)
    setHandoffMessage(settings?.handoff_message ?? '')
    setPageControl(settings?.page_control?.enabled ?? false)
    setAllowPageActions(settings?.page_control?.allow_actions ?? false)
    setClientActionsEnabled(settings?.client_actions?.enabled ?? true)
    const tools = (agent.tools as ToolConfig[]) ?? []
    const map: Record<string, ToolPolicy> = {}
    for (const t of BUILTIN_TOOLS) {
      map[t.key] = tools.find((x) => x.key === t.key)?.policy ?? t.defaultPolicy
    }
    for (const t of tools) map[t.key] = t.policy
    setPolicies(map)
  }, [agent])

  // Ensure custom-action rows have a default policy in the map.
  useEffect(() => {
    setPolicies((prev) => {
      const next = { ...prev }
      let changed = false
      for (const row of toolRows) {
        if (!(row.key in next)) {
          next[row.key] = row.key.startsWith('action:') ? DEFAULT_ACTION_POLICY : 'auto'
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [toolRows])

  const saveMutation = useMutation({
    mutationFn: () => {
      // Spread the server settings first so keys owned elsewhere (e.g. the MCP
      // channel card's settings.mcp) survive a builder save.
      const settings: AgentSettings = {
        ...((agent?.settings ?? {}) as Partial<AgentSettings>),
        retrieval: { enabled: retrievalEnabled, k: retrievalK, source_ids: sourceIds },
        handoff_message: handoffMessage,
        guardrails: { max_tool_calls: maxToolCalls, require_citations: requireCitations },
        page_control: { enabled: pageControl, allow_actions: pageControl && allowPageActions },
        client_actions: { enabled: clientActionsEnabled },
      }
      const tools: ToolConfig[] = toolRows.map((row) => ({
        key: row.key,
        policy: policies[row.key] ?? 'auto',
      }))
      return aiApi.updateAgent(agentId, {
        name: name.trim(),
        description: description.trim() || null,
        avatar_emoji: avatar || null,
        status,
        model_ref: modelRef || null,
        system_prompt: systemPrompt,
        temperature: tempEnabled ? temperature : null,
        settings,
        tools,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: aiKeys.agent(workspaceId, agentId) })
      queryClient.invalidateQueries({ queryKey: aiKeys.agents(workspaceId) })
      toast.success(t('ai.agent_saved'))
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Save failed'),
  })

  if (isLoading || !agent) {
    return (
      <PageShell>
        <div className="flex h-full items-center justify-center">
          <Spinner />
        </div>
      </PageShell>
    )
  }

  const chatModels = (models.data ?? []).filter((m) => m.modality === 'chat')

  function toggleSource(id: string) {
    setSourceIds((prev) => {
      if (prev == null) return [id]
      return prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    })
  }

  return (
    <PageShell>
      <PageHeader
        title={name || 'Agent'}
        description={t('ai.configure_behaviour_tools_and_guardrails')}
        back={
          <Button variant="ghost" size="icon" asChild aria-label={t('ai.back_to_agents')}>
            <Link to="/ai/agents">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
        }
        actions={
          canManage ? (
            <>
              <NativeSelect
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                aria-label={t('ai.agent_status')}
                size="sm"
              >
                <NativeSelectOption value="draft">{t('common.draft')}</NativeSelectOption>
                <NativeSelectOption value="live">{t('ai.live')}</NativeSelectOption>
                <NativeSelectOption value="off">{t('common.off')}</NativeSelectOption>
              </NativeSelect>
              <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                Save
              </Button>
            </>
          ) : null
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <div className="grid h-full grid-cols-1 lg:grid-cols-[1fr_minmax(360px,40%)]">
          <div className="space-y-6 p-4 sm:p-6 lg:overflow-y-auto">
            {/* Identity */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('ai.identity')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid grid-cols-[80px_1fr] gap-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor="agent-avatar">{t('ai.avatar')}</Label>
                    <Input
                      id="agent-avatar"
                      value={avatar}
                      onChange={(e) => setAvatar(e.target.value)}
                      maxLength={4}
                      className="text-center text-lg"
                      disabled={!canManage}
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="agent-name">{t('common.name')}</Label>
                    <Input
                      id="agent-name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      disabled={!canManage}
                    />
                  </div>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="agent-desc">{t('common.description')}</Label>
                  <Input
                    id="agent-desc"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder={t('ai.what_this_agent_does')}
                    disabled={!canManage}
                  />
                </div>
              </CardContent>
            </Card>

            {/* Model */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('ai.model')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-1.5">
                  <Label htmlFor="agent-model">{t('ai.model')}</Label>
                  <NativeSelect
                    id="agent-model"
                    value={modelRef}
                    onChange={(e) => setModelRef(e.target.value)}
                    className="w-full"
                    disabled={!canManage}
                  >
                    <NativeSelectOption value="">{t('common.workspace_default')}</NativeSelectOption>
                    {chatModels.map((m) => (
                      <NativeSelectOption
                        key={`${m.provider_id}:${m.model_key}`}
                        value={`${m.provider_id}:${m.model_key}`}
                      >
                        {m.display_name} · {m.provider_name}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="agent-temp-toggle">{t('ai.override_temperature')}</Label>
                    <Switch
                      id="agent-temp-toggle"
                      checked={tempEnabled}
                      onCheckedChange={setTempEnabled}
                      disabled={!canManage}
                    />
                  </div>
                  {tempEnabled ? (
                    <div className="flex items-center gap-3">
                      <Slider
                        value={[temperature]}
                        min={0}
                        max={2}
                        step={0.1}
                        onValueChange={(v) => setTemperature(v[0])}
                        disabled={!canManage}
                        className="flex-1"
                      />
                      <span className="w-10 text-right text-sm tabular-nums text-muted-foreground">
                        {temperature.toFixed(1)}
                      </span>
                    </div>
                  ) : null}
                </div>
              </CardContent>
            </Card>

            {/* System prompt */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('ai.system_prompt')}</CardTitle>
              </CardHeader>
              <CardContent>
                <Textarea
                  aria-label={t('ai.system_prompt')}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  rows={7}
                  disabled={!canManage}
                  placeholder={t('ai.you_are_a_friendly_support_agent')}
                  className="text-sm"
                />
              </CardContent>
            </Card>

            {/* Retrieval */}
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{t('ai.knowledge_retrieval')}</CardTitle>
                <Switch
                  checked={retrievalEnabled}
                  onCheckedChange={setRetrievalEnabled}
                  aria-label={t('ai.enable_retrieval')}
                  disabled={!canManage}
                />
              </CardHeader>
              {retrievalEnabled ? (
                <CardContent className="grid gap-4">
                  <div className="grid gap-2">
                    <div className="flex items-center justify-between">
                      <Label>Chunks retrieved (k)</Label>
                      <span className="text-sm tabular-nums text-muted-foreground">{retrievalK}</span>
                    </div>
                    <Slider
                      value={[retrievalK]}
                      min={1}
                      max={20}
                      step={1}
                      onValueChange={(v) => setRetrievalK(v[0])}
                      disabled={!canManage}
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label>{t('ai.scope_to_sources')}</Label>
                    <p className="text-xs text-muted-foreground">
                      {sourceIds == null ? 'Searching all sources' : `${sourceIds.length} selected`}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {(sources.data ?? []).map((source) => {
                        const active = sourceIds?.includes(source.id) ?? false
                        return (
                          <button
                            key={source.id}
                            type="button"
                            onClick={() => toggleSource(source.id)}
                            aria-pressed={active}
                            disabled={!canManage}
                          >
                            <Badge variant={active ? 'default' : 'outline'} className="cursor-pointer">
                              {source.name}
                            </Badge>
                          </button>
                        )
                      })}
                      {sourceIds != null ? (
                        <button type="button" onClick={() => setSourceIds(null)} disabled={!canManage}>
                          <Badge variant="ghost" className="cursor-pointer text-muted-foreground">
                            Clear (all)
                          </Badge>
                        </button>
                      ) : null}
                    </div>
                  </div>
                </CardContent>
              ) : null}
            </Card>

            {/* In-app guidance */}
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle className="text-sm">{t('ai.in_app_guidance')}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t('ai.let_this_agent_see_the_page')}
                  </p>
                </div>
                <Switch
                  checked={pageControl}
                  onCheckedChange={setPageControl}
                  aria-label={t('ai.enable_in_app_guidance')}
                  disabled={!canManage}
                />
              </CardHeader>
              {pageControl ? (
                <CardContent className="grid gap-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="grid gap-1">
                      <Label htmlFor="allow-page-actions">{t('ai.let_it_act_on_the_page')}</Label>
                      <p className="text-xs text-muted-foreground">
                        Click, type and navigate on the visitor&rsquo;s behalf. Each visitor still
                        has to allow it in their own conversation, and password fields are never
                        touched.
                      </p>
                    </div>
                    <Switch
                      id="allow-page-actions"
                      checked={allowPageActions}
                      onCheckedChange={setAllowPageActions}
                      aria-label={t('ai.allow_page_actions')}
                      disabled={!canManage}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t('common.add')} <code>data-stept-no-ai</code> to any element on your site to fence it off
                    from the assistant.
                  </p>
                </CardContent>
              ) : null}
            </Card>

            {/* Client actions (SDK-registered) */}
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle className="text-sm">{t('ai.client_actions')}</CardTitle>
                  <p className="text-xs text-muted-foreground">
                    {t('ai.functions_your_site_registers_with')} <code>Stept(&apos;action&apos;, …)</code>{' '}
                    become tools this agent can run in the visitor&rsquo;s browser. Each action
                    decides whether the visitor confirms first or your team approves.
                  </p>
                </div>
                <Switch
                  checked={clientActionsEnabled}
                  onCheckedChange={setClientActionsEnabled}
                  aria-label={t('ai.enable_client_actions')}
                  disabled={!canManage}
                />
              </CardHeader>
            </Card>

            {/* MCP channel */}
            <McpChannelCard agent={agent} />

            {/* Tools */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Tools &amp; policies</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <ToolPolicyMatrix
                  rows={toolRows}
                  value={policies}
                  onChange={(key, policy) => setPolicies((prev) => ({ ...prev, [key]: policy }))}
                  disabled={!canManage}
                />
                <CustomActionsSection />
              </CardContent>
            </Card>

            {/* Guardrails */}
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('ai.guardrails')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <Label>{t('ai.max_tool_calls_per_run')}</Label>
                    <span className="text-sm tabular-nums text-muted-foreground">{maxToolCalls}</span>
                  </div>
                  <Slider
                    value={[maxToolCalls]}
                    min={1}
                    max={30}
                    step={1}
                    onValueChange={(v) => setMaxToolCalls(v[0])}
                    disabled={!canManage}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <Label htmlFor="require-citations">{t('ai.require_citations')}</Label>
                    <p className="text-xs text-muted-foreground">
                      {t('ai.replies_must_cite_retrieved_sources')}
                    </p>
                  </div>
                  <Switch
                    id="require-citations"
                    checked={requireCitations}
                    onCheckedChange={setRequireCitations}
                    disabled={!canManage}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="handoff-message">{t('ai.handoff_message')}</Label>
                  <Textarea
                    id="handoff-message"
                    rows={2}
                    value={handoffMessage}
                    onChange={(e) => setHandoffMessage(e.target.value)}
                    disabled={!canManage}
                    placeholder={t('ai.let_me_connect_you_with_a')}
                  />
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Sandbox */}
          <div className="min-h-[500px] border-t lg:min-h-0 lg:border-l lg:border-t-0">
            <TestSandbox agentId={agentId} />
          </div>
        </div>
      </div>
    </PageShell>
  )
}

export default Component
