/**
 * Create/edit dialog for knowledge sources.
 * Create: pick a type (files, URLs, text, sitemap, crawl, GitHub, Notion) then fill
 * its config; remote types kick off an initial sync. Edit: type is fixed, config and
 * secrets are editable — stored secrets stay blank with an "unchanged" placeholder.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  FileText,
  Github,
  Globe,
  Link2,
  Loader2,
  Map,
  NotebookText,
  Type,
  Upload,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { currentWorkspaceId } from '@/stores/auth'
import { formatBytes } from '@/lib/format'

import { knowledgeApi, knowledgeKeys, type Source, type SourceType } from '../api'
import { refreshMinutes as configuredRefresh } from '../lib'

const TYPE_TABS: { value: SourceType; label: string; icon: LucideIcon }[] = [
  { value: 'files', label: 'Files', icon: Upload },
  { value: 'urls', label: 'URLs', icon: Link2 },
  { value: 'text', label: 'Text', icon: Type },
  { value: 'sitemap', label: 'Sitemap', icon: Map },
  { value: 'crawl', label: 'Crawl', icon: Globe },
  { value: 'github', label: 'GitHub', icon: Github },
  { value: 'notion', label: 'Notion', icon: NotebookText },
]

/** Types whose documents live remotely — they get an initial sync + optional auto re-sync. */
const REMOTE_TYPES: SourceType[] = ['urls', 'sitemap', 'crawl', 'github', 'notion']

const DEFAULT_NAMES: Record<SourceType, string> = {
  files: 'File upload',
  urls: 'Web pages',
  text: 'Pasted text',
  sitemap: 'Sitemap',
  crawl: 'Web crawl',
  github: 'GitHub repository',
  notion: 'Notion workspace',
}

interface FormState {
  name: string
  files: File[]
  urls: string
  title: string
  content: string
  sitemapUrl: string
  baseUrl: string
  maxPages: string
  maxDepth: string
  repoOwner: string
  repo: string
  branch: string
  includeFiles: boolean
  includeIssues: boolean
  includePrs: boolean
  rootPageId: string
  token: string
  refreshMinutes: string
}

const EMPTY_FORM: FormState = {
  name: '',
  files: [],
  urls: '',
  title: '',
  content: '',
  sitemapUrl: '',
  baseUrl: '',
  maxPages: '',
  maxDepth: '',
  repoOwner: '',
  repo: '',
  branch: '',
  includeFiles: true,
  includeIssues: false,
  includePrs: false,
  rootPageId: '',
  token: '',
  refreshMinutes: '',
}

function formFromSource(source: Source): FormState {
  const config = source.config ?? {}
  const str = (key: string) => {
    const value = config[key]
    return typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
  }
  return {
    ...EMPTY_FORM,
    name: source.name,
    urls: Array.isArray(config.urls) ? (config.urls as string[]).join('\n') : '',
    sitemapUrl: str('sitemap_url'),
    baseUrl: str('base_url'),
    maxPages: str('max_pages'),
    maxDepth: str('max_depth'),
    repoOwner: str('repo_owner'),
    repo: str('repo'),
    branch: str('branch'),
    includeFiles: config.include_files !== false,
    includeIssues: config.include_issues === true,
    includePrs: config.include_prs === true,
    rootPageId: str('root_page_id'),
    refreshMinutes: configuredRefresh(config)?.toString() ?? '',
  }
}

function positiveInt(raw: string): number | null {
  const value = Number(raw.trim())
  if (!raw.trim() || !Number.isFinite(value) || value <= 0) return null
  return Math.round(value)
}

/** Build the config payload for a type from the form (only fields the user filled). */
function buildConfig(mode: SourceType, form: FormState): Record<string, unknown> {
  const config: Record<string, unknown> = {}
  const maxPages = positiveInt(form.maxPages)
  if (mode === 'urls') {
    config.urls = form.urls
      .split('\n')
      .map((u) => u.trim())
      .filter(Boolean)
  }
  if (mode === 'sitemap') {
    config.sitemap_url = form.sitemapUrl.trim()
    if (maxPages !== null) config.max_pages = maxPages
  }
  if (mode === 'crawl') {
    config.base_url = form.baseUrl.trim()
    if (maxPages !== null) config.max_pages = maxPages
    const maxDepth = positiveInt(form.maxDepth)
    if (maxDepth !== null) config.max_depth = maxDepth
  }
  if (mode === 'github') {
    config.repo_owner = form.repoOwner.trim()
    config.repo = form.repo.trim()
    if (form.branch.trim()) config.branch = form.branch.trim()
    config.include_files = form.includeFiles
    config.include_issues = form.includeIssues
    config.include_prs = form.includePrs
  }
  if (mode === 'notion') {
    if (form.rootPageId.trim()) config.root_page_id = form.rootPageId.trim()
    if (maxPages !== null) config.max_pages = maxPages
  }
  if (REMOTE_TYPES.includes(mode)) {
    const refresh = positiveInt(form.refreshMinutes)
    if (refresh !== null && refresh >= 5) config.refresh_minutes = refresh
  }
  return config
}

function validate(mode: SourceType, form: FormState, editing: boolean): string | null {
  if (mode === 'files' && !editing && form.files.length === 0) return 'Choose at least one file.'
  if (mode === 'urls' && !form.urls.trim()) return 'Add at least one URL.'
  if (mode === 'text' && !editing && (!form.title.trim() || !form.content.trim()))
    return 'Title and content are required.'
  if (mode === 'sitemap' && !form.sitemapUrl.trim()) return 'Sitemap URL is required.'
  if (mode === 'crawl' && !form.baseUrl.trim()) return 'Base URL is required.'
  if (mode === 'github' && (!form.repoOwner.trim() || !form.repo.trim()))
    return 'Repository owner and name are required.'
  if (mode === 'notion' && !editing && !form.token.trim())
    return 'A Notion integration token is required.'
  if (form.refreshMinutes.trim()) {
    const refresh = positiveInt(form.refreshMinutes)
    if (refresh === null || refresh < 5) return 'Auto re-sync interval must be at least 5 minutes.'
  }
  return null
}

/** FastAPI 422 details ({loc, msg}[]) → readable lines; anything else → nothing extra. */
function detailLines(error: unknown): string[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return []
  return (error.details as unknown[]).flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const { loc, msg } = item as { loc?: unknown[]; msg?: unknown }
    if (typeof msg !== 'string') return []
    const field = Array.isArray(loc) ? loc.filter((p) => typeof p === 'string').join('.') : ''
    return [field ? `${field}: ${msg}` : msg]
  })
}

export function AddSourceDialog({
  open,
  onOpenChange,
  onCreated,
  source,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (source: Source) => void
  /** When set, the dialog edits this source instead of creating a new one. */
  source?: Source
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const editing = Boolean(source)
  const [mode, setMode] = useState<SourceType>((source?.type as SourceType) ?? 'files')
  const [form, setForm] = useState<FormState>(source ? formFromSource(source) : EMPTY_FORM)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const mutation = useMutation({
    mutationFn: async (): Promise<Source> => {
      const trimmed = form.name.trim()
      const secrets = form.token.trim() ? { token: form.token.trim() } : undefined

      if (editing && source) {
        return knowledgeApi.updateSource(source.id, {
          name: trimmed || source.name,
          config: { ...source.config, ...buildConfig(mode, form) },
          ...(secrets ? { secrets } : {}),
        })
      }

      const name = trimmed || DEFAULT_NAMES[mode]
      if (mode === 'files') {
        const created = await knowledgeApi.createSource({ type: 'files', name })
        for (const file of form.files) {
          await knowledgeApi.uploadDocument(created.id, file)
        }
        return created
      }
      if (mode === 'text') {
        const created = await knowledgeApi.createSource({ type: 'text', name })
        await knowledgeApi.addTextDocument(created.id, {
          title: form.title.trim(),
          content: form.content,
        })
        return created
      }
      const created = await knowledgeApi.createSource({
        type: mode,
        name,
        config: buildConfig(mode, form),
        ...(secrets ? { secrets } : {}),
      })
      await knowledgeApi.syncSource(created.id)
      return created
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.sources(workspaceId) })
      if (editing && source) {
        queryClient.invalidateQueries({ queryKey: knowledgeKeys.source(workspaceId, source.id) })
      }
      toast.success(editing ? 'Source updated' : 'Source created')
      setForm(EMPTY_FORM)
      onOpenChange(false)
      onCreated?.(result)
    },
  })

  const { reset: resetMutation } = mutation

  // Re-seed when the dialog opens (edit dialogs can be reused across sources).
  useEffect(() => {
    if (open) {
      setForm(source ? formFromSource(source) : EMPTY_FORM)
      setMode((source?.type as SourceType) ?? 'files')
      resetMutation()
    }
    // Only re-run when visibility or the target source changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source?.id, resetMutation])

  const validationError = validate(mode, form, editing)
  const apiErrorLines = mutation.isError ? detailLines(mutation.error) : []

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) onOpenChange(next)
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? 'Edit source' : 'Add a knowledge source'}</DialogTitle>
          <DialogDescription>
            {editing
              ? 'Update this source’s settings. Changes apply on the next sync.'
              : 'Import content your AI agents and help center can search over.'}
          </DialogDescription>
        </DialogHeader>

        <Tabs value={mode} onValueChange={(v) => setMode(v as SourceType)}>
          {!editing ? (
            <TabsList className="grid h-auto w-full grid-cols-4">
              {TYPE_TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value} className="flex-col gap-1 py-2">
                  <tab.icon className="size-4" /> {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          ) : null}

          <div className="mt-4 grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="source-name">Source name</Label>
              <Input
                id="source-name"
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                placeholder="e.g. Product documentation"
              />
            </div>

            <TabsContent value="files" className="mt-0">
              {editing ? (
                <p className="text-sm text-muted-foreground">
                  Add or remove documents from the source page.
                </p>
              ) : (
                <FileDrop files={form.files} onChange={(files) => set('files', files)} />
              )}
            </TabsContent>

            <TabsContent value="urls" className="mt-0 grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="source-urls">URLs (one per line)</Label>
                <Textarea
                  id="source-urls"
                  value={form.urls}
                  onChange={(e) => set('urls', e.target.value)}
                  rows={5}
                  placeholder={'https://docs.example.com/getting-started\nhttps://example.com/faq'}
                  className="font-mono text-xs"
                />
                <p className="text-xs text-muted-foreground">
                  Each page is fetched, parsed and indexed after you save.
                </p>
              </div>
              <RefreshField value={form.refreshMinutes} onChange={(v) => set('refreshMinutes', v)} />
            </TabsContent>

            <TabsContent value="text" className="mt-0 grid gap-3">
              {editing ? (
                <p className="text-sm text-muted-foreground">
                  Add or remove documents from the source page.
                </p>
              ) : (
                <>
                  <div className="grid gap-1.5">
                    <Label htmlFor="source-title">Document title</Label>
                    <Input
                      id="source-title"
                      value={form.title}
                      onChange={(e) => set('title', e.target.value)}
                      placeholder="Refund policy"
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="source-content">Content (markdown)</Label>
                    <Textarea
                      id="source-content"
                      value={form.content}
                      onChange={(e) => set('content', e.target.value)}
                      rows={8}
                      placeholder="Paste or write the content you want indexed…"
                    />
                  </div>
                </>
              )}
            </TabsContent>

            <TabsContent value="sitemap" className="mt-0 grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="sitemap-url">Sitemap URL</Label>
                <Input
                  id="sitemap-url"
                  value={form.sitemapUrl}
                  onChange={(e) => set('sitemapUrl', e.target.value)}
                  placeholder="https://example.com/sitemap.xml"
                />
                <p className="text-xs text-muted-foreground">
                  Every page listed in the sitemap is fetched and indexed.
                </p>
              </div>
              <NumberField
                id="sitemap-max-pages"
                label="Max pages (optional)"
                value={form.maxPages}
                onChange={(v) => set('maxPages', v)}
              />
              <RefreshField value={form.refreshMinutes} onChange={(v) => set('refreshMinutes', v)} />
            </TabsContent>

            <TabsContent value="crawl" className="mt-0 grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="crawl-base-url">Base URL</Label>
                <Input
                  id="crawl-base-url"
                  value={form.baseUrl}
                  onChange={(e) => set('baseUrl', e.target.value)}
                  placeholder="https://docs.example.com"
                />
                <p className="text-xs text-muted-foreground">
                  Follows same-site links from this page and indexes what it finds.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <NumberField
                  id="crawl-max-pages"
                  label="Max pages (optional)"
                  value={form.maxPages}
                  onChange={(v) => set('maxPages', v)}
                />
                <NumberField
                  id="crawl-max-depth"
                  label="Max depth (optional)"
                  value={form.maxDepth}
                  onChange={(v) => set('maxDepth', v)}
                />
              </div>
              <RefreshField value={form.refreshMinutes} onChange={(v) => set('refreshMinutes', v)} />
            </TabsContent>

            <TabsContent value="github" className="mt-0 grid gap-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="github-owner">Repository owner</Label>
                  <Input
                    id="github-owner"
                    value={form.repoOwner}
                    onChange={(e) => set('repoOwner', e.target.value)}
                    placeholder="acme"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="github-repo">Repository</Label>
                  <Input
                    id="github-repo"
                    value={form.repo}
                    onChange={(e) => set('repo', e.target.value)}
                    placeholder="docs"
                  />
                </div>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="github-branch">Branch (optional)</Label>
                <Input
                  id="github-branch"
                  value={form.branch}
                  onChange={(e) => set('branch', e.target.value)}
                  placeholder="main"
                />
              </div>
              <fieldset className="grid gap-2">
                <legend className="text-sm font-medium">Include</legend>
                <CheckField
                  id="github-include-files"
                  label="Markdown & doc files"
                  checked={form.includeFiles}
                  onChange={(v) => set('includeFiles', v)}
                />
                <CheckField
                  id="github-include-issues"
                  label="Issues"
                  checked={form.includeIssues}
                  onChange={(v) => set('includeIssues', v)}
                />
                <CheckField
                  id="github-include-prs"
                  label="Pull requests"
                  checked={form.includePrs}
                  onChange={(v) => set('includePrs', v)}
                />
              </fieldset>
              <SecretField
                id="github-token"
                label="Access token (optional)"
                value={form.token}
                onChange={(v) => set('token', v)}
                hasStored={editing && Boolean(source?.has_secrets)}
                hint="Needed for private repositories and higher rate limits."
              />
              <RefreshField value={form.refreshMinutes} onChange={(v) => set('refreshMinutes', v)} />
            </TabsContent>

            <TabsContent value="notion" className="mt-0 grid gap-3">
              <SecretField
                id="notion-token"
                label="Integration token"
                value={form.token}
                onChange={(v) => set('token', v)}
                hasStored={editing && Boolean(source?.has_secrets)}
                hint="Create an internal integration in Notion and share pages with it."
              />
              <div className="grid gap-1.5">
                <Label htmlFor="notion-root">Root page ID (optional)</Label>
                <Input
                  id="notion-root"
                  value={form.rootPageId}
                  onChange={(e) => set('rootPageId', e.target.value)}
                  placeholder="Leave empty to import everything shared with the integration"
                />
              </div>
              <NumberField
                id="notion-max-pages"
                label="Max pages (optional)"
                value={form.maxPages}
                onChange={(v) => set('maxPages', v)}
              />
              <RefreshField value={form.refreshMinutes} onChange={(v) => set('refreshMinutes', v)} />
            </TabsContent>
          </div>
        </Tabs>

        {mutation.isError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <p>
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Could not save source'}
            </p>
            {apiErrorLines.length > 0 ? (
              <ul className="mt-1 list-inside list-disc text-xs">
                {apiErrorLines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={validationError !== null || mutation.isPending}
            title={validationError ?? undefined}
          >
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            {editing ? 'Save changes' : 'Create source'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RefreshField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor="source-refresh">Auto re-sync every N minutes (min 5)</Label>
      <Input
        id="source-refresh"
        type="number"
        min={5}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Leave empty to sync manually"
      />
    </div>
  )
}

function NumberField({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" min={1} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

function SecretField({
  id,
  label,
  value,
  onChange,
  hasStored,
  hint,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  hasStored: boolean
  hint?: string
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={hasStored ? '•••••••• (unchanged)' : ''}
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {hasStored ? (
        <p className="text-xs text-muted-foreground">Leave blank to keep the stored token.</p>
      ) : null}
    </div>
  )
}

function CheckField({
  id,
  label,
  checked,
  onChange,
}: {
  id: string
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
    </div>
  )
}

function FileDrop({ files, onChange }: { files: File[]; onChange: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false)
  return (
    <div className="grid gap-2">
      <label
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          onChange([...files, ...Array.from(e.dataTransfer.files)])
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center text-sm transition-colors hover:bg-accent/50 ${
          dragging ? 'border-primary bg-accent/50' : ''
        }`}
      >
        <FileText className="size-6 text-muted-foreground" />
        <span className="font-medium">Drop files or click to browse</span>
        <span className="text-xs text-muted-foreground">PDF, DOCX, HTML, Markdown, CSV or TXT</span>
        <input
          type="file"
          multiple
          className="sr-only"
          aria-label="Upload files"
          onChange={(e) => onChange([...files, ...Array.from(e.target.files ?? [])])}
        />
      </label>
      {files.length > 0 ? (
        <ul className="grid gap-1">
          {files.map((file, i) => (
            <li
              key={`${file.name}-${i}`}
              className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
            >
              <span className="truncate">{file.name}</span>
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                {formatBytes(file.size)}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onChange(files.filter((_, idx) => idx !== i))}
                  className="rounded p-0.5 hover:bg-muted"
                >
                  <X className="size-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
