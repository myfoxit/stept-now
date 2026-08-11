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
import { t } from '@/i18n'

export function Component() {
  const [query, setQuery] = useState('')
  const [k, setK] = useState(8)
  const [scoped, setScoped] = useState<string[]>([])
  const [rerank, setRerank] = useState(false)
  const sources = useSources()
  const search = useSearch()

  function run(opts?: { rerank?: boolean }) {
    if (!query.trim()) return
    const useRerank = opts?.rerank ?? rerank
    search.mutate({
      query: query.trim(),
      k,
      source_ids: scoped.length ? scoped : null,
      ...(useRerank ? { rerank: true } : {}),
    })
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    run()
  }

  /**
   * Re-run immediately. Toggling used to leave the previous results on screen
   * unchanged, which reads as "reranking made no difference" when in fact
   * nothing had been reranked.
   */
  function onRerankChange(next: boolean) {
    setRerank(next)
    if (search.data) run({ rerank: next })
  }

  function toggleSource(id: string) {
    setScoped((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]))
  }

  const results = search.data?.results ?? []
  const maxScore = results.reduce((max, r) => Math.max(max, r.score), 0) || 1

  return (
    <PageShell>
      <PageHeader
        title={t('knowledge.search_playground')}
        description={t('knowledge.query_your_knowledge_base_exactly_like')}
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
                placeholder={t('knowledge.e_g_how_do_i_install')}
                aria-label={t('knowledge.search_query')}
                className="pl-9"
              />
            </div>
            <NativeSelect
              value={String(k)}
              onChange={(e) => setK(Number(e.target.value))}
              aria-label={t('knowledge.number_of_results')}
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
              <Switch id="rerank-toggle" checked={rerank} onCheckedChange={onRerankChange} />
              <Label htmlFor="rerank-toggle" className="text-sm font-medium">
                {t('knowledge.rerank_with_ai')}
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {t('knowledge.one_extra_llm_pass_reorders_results')}
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
              <EmptyTitle>{t('knowledge.no_matches')}</EmptyTitle>
              <EmptyDescription>{t('knowledge.try_a_different_query_or_broaden')}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : null}

        {!search.data && !search.isPending ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Sparkles />
              </EmptyMedia>
              <EmptyTitle>{t('knowledge.test_your_retrieval_quality')}</EmptyTitle>
              <EmptyDescription>
                {t('knowledge.enter_a_question_above_to_see')}
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
                    className="flex shrink-0 items-center gap-1 text-xs text-brand hover:underline"
                  >
                    {t('common.open')} <ExternalLink className="size-3" />
                  </a>
                ) : null}
              </div>
              <div className="mb-2 flex items-center gap-2">
                <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn('h-full rounded-full bg-brand')}
                    style={{ width: `${Math.max(6, (result.score / maxScore) * 100)}%` }}
                  />
                </div>
                {/*
                  The raw number is a reciprocal-rank-fusion score (~1/(60+rank)),
                  so the top hit reads as "0.0164" — indistinguishable from
                  irrelevant. Show strength relative to the best hit instead and
                  keep the raw value in the tooltip for debugging.
                */}
                <span
                  className="text-xs tabular-nums text-muted-foreground"
                  title={`Fused score ${result.score.toFixed(4)}`}
                >
                  {Math.round((result.score / maxScore) * 100)}% of top hit
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
