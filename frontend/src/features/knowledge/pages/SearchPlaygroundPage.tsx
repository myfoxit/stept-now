import { ExternalLink, Search, Sparkles } from 'lucide-react'
import { useState } from 'react'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Spinner } from '@/components/ui/spinner'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

import { KnowledgeNav, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useSearch, useSources } from '../hooks'

export function Component() {
  const [query, setQuery] = useState('')
  const [k, setK] = useState(8)
  const [scoped, setScoped] = useState<string[]>([])
  const [rerank, setRerank] = useState(false)
  const sources = useSources()
  const search = useSearch()

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    search.mutate({
      query: query.trim(),
      k,
      source_ids: scoped.length ? scoped : null,
      ...(rerank ? { rerank: true } : {}),
    })
  }

  function toggleSource(id: string) {
    setScoped((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]))
  }

  const results = search.data?.results ?? []
  const maxScore = results.reduce((max, r) => Math.max(max, r.score), 0) || 1

  return (
    <PageShell>
      <PageHeader
        title="Search playground"
        description="Query your knowledge base exactly like your AI agents do"
      />
      <KnowledgeNav />
      <ScrollBody className="space-y-5">
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. How do I install the chat widget?"
                aria-label="Search query"
                className="pl-9"
              />
            </div>
            <NativeSelect
              value={String(k)}
              onChange={(e) => setK(Number(e.target.value))}
              aria-label="Number of results"
              className="sm:w-32"
            >
              {[4, 8, 12, 20].map((n) => (
                <NativeSelectOption key={n} value={n}>
                  Top {n}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <Button type="submit" disabled={!query.trim() || search.isPending}>
              {search.isPending ? <Spinner className="size-4" /> : <Search className="size-4" />}
              Search
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <div className="flex items-center gap-2">
              <Switch id="rerank-toggle" checked={rerank} onCheckedChange={setRerank} />
              <Label htmlFor="rerank-toggle" className="text-sm font-medium">
                Rerank with AI
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              One extra LLM pass reorders results — falls back to fused order on failure.
            </p>
          </div>

          {sources.data && sources.data.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-muted-foreground">Scope:</span>
              {sources.data.map((source) => {
                const active = scoped.includes(source.id)
                return (
                  <button
                    key={source.id}
                    type="button"
                    onClick={() => toggleSource(source.id)}
                    aria-pressed={active}
                  >
                    <Badge variant={active ? 'default' : 'outline'} className="cursor-pointer">
                      {source.name}
                    </Badge>
                  </button>
                )
              })}
            </div>
          ) : null}
        </form>

        {search.isError ? (
          <p className="text-sm text-destructive">
            {search.error instanceof ApiError ? search.error.message : 'Search failed'}
          </p>
        ) : null}

        {search.isSuccess ? (
          <p className="text-xs text-muted-foreground">
            {results.length} result{results.length === 1 ? '' : 's'} in{' '}
            {Math.round(search.data.latency_ms)} ms
          </p>
        ) : null}

        {search.isSuccess && results.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Search />
              </EmptyMedia>
              <EmptyTitle>No matches</EmptyTitle>
              <EmptyDescription>Try a different query or broaden the scope.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : null}

        {!search.data && !search.isPending ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Sparkles />
              </EmptyMedia>
              <EmptyTitle>Test your retrieval quality</EmptyTitle>
              <EmptyDescription>
                Enter a question above to see the ranked chunks and relevance scores your agents
                retrieve.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : null}

        <ul className="space-y-3">
          {results.map((result, i) => (
            <li key={result.chunk_id} className="rounded-lg border p-4">
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold tabular-nums">
                    {i + 1}
                  </span>
                  <span className="truncate font-medium">{result.title || 'Untitled'}</span>
                </div>
                {result.url ? (
                  <a
                    href={result.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
                  >
                    Open <ExternalLink className="size-3" />
                  </a>
                ) : null}
              </div>
              <div className="mb-2 flex items-center gap-2">
                <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn('h-full rounded-full bg-primary')}
                    style={{ width: `${Math.max(6, (result.score / maxScore) * 100)}%` }}
                  />
                </div>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {result.score.toFixed(4)}
                </span>
              </div>
              <p className="line-clamp-4 whitespace-pre-wrap text-sm text-muted-foreground">
                {result.content}
              </p>
            </li>
          ))}
        </ul>
      </ScrollBody>
    </PageShell>
  )
}

export default Component
