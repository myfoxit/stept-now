/**
 * Inline article editor: title, collection, publish and a three-tab body
 * editor. "Write" is the rich text surface, "Markdown" is the raw source and
 * "Preview" renders it the way the help center will. All three read and write
 * the same markdown string, so switching tabs never loses content.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Code2, Eye, Globe, Loader2, PencilLine, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { RichTextEditor } from '@/components/editor/RichTextEditor'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Spinner } from '@/components/ui/spinner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, type Collection } from '../api'
import { useArticle } from '../hooks'
import { Markdown } from './markdown'

export function ArticleEditor({
  articleId,
  collections,
  onDeleted,
}: {
  articleId: string
  collections: Collection[]
  onDeleted: () => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const canWrite = useHasPerm('knowledge:write')
  const { data: article, isLoading } = useArticle(articleId)

  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [collectionId, setCollectionId] = useState<string>('')
  const [tab, setTab] = useState('write')

  useEffect(() => {
    if (article) {
      setTitle(article.title)
      setBody(article.body)
      setCollectionId(article.collection_id ?? '')
    }
  }, [article])

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: knowledgeKeys.article(workspaceId, articleId) })
    queryClient.invalidateQueries({ queryKey: ['knowledge', workspaceId, 'articles'] })
    queryClient.invalidateQueries({ queryKey: knowledgeKeys.collections(workspaceId) })
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      knowledgeApi.updateArticle(articleId, {
        title: title.trim(),
        body,
        collection_id: collectionId || null,
      }),
    onSuccess: () => {
      invalidate()
      toast.success('Article saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Save failed'),
  })

  const publishMutation = useMutation({
    mutationFn: (publish: boolean) =>
      publish ? knowledgeApi.publishArticle(articleId) : knowledgeApi.unpublishArticle(articleId),
    onSuccess: (_data, publish) => {
      invalidate()
      toast.success(publish ? 'Article published' : 'Article unpublished')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Action failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => knowledgeApi.deleteArticle(articleId),
    onSuccess: () => {
      invalidate()
      toast.success('Article deleted')
      onDeleted()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  if (isLoading || !article) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  const published = article.status === 'published'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b p-4">
        <Badge variant={published ? 'default' : 'secondary'} className="gap-1">
          {published ? <Globe className="size-3" /> : <PencilLine className="size-3" />}
          {published ? 'Published' : 'Draft'}
        </Badge>
        <div className="ml-auto flex items-center gap-2">
          {canWrite ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending || !title.trim()}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                Save
              </Button>
              <Button
                size="sm"
                variant={published ? 'secondary' : 'default'}
                onClick={() => publishMutation.mutate(!published)}
                disabled={publishMutation.isPending}
              >
                <Globe className="size-4" />
                {published ? 'Unpublish' : 'Publish'}
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="size-8 text-destructive"
                aria-label="Delete article"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="size-4" />
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_220px]">
          <div className="grid gap-1.5">
            <Label htmlFor="article-title">Title</Label>
            <Input
              id="article-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={!canWrite}
              className="text-base font-medium"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="article-collection">Collection</Label>
            <NativeSelect
              id="article-collection"
              value={collectionId}
              onChange={(e) => setCollectionId(e.target.value)}
              disabled={!canWrite}
              className="w-full"
            >
              <NativeSelectOption value="">Uncategorised</NativeSelectOption>
              {collections.map((c) => (
                <NativeSelectOption key={c.id} value={c.id}>
                  {c.icon ? `${c.icon} ` : ''}
                  {c.name}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
        </div>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="write">
              <PencilLine className="size-4" /> Write
            </TabsTrigger>
            <TabsTrigger value="markdown">
              <Code2 className="size-4" /> Markdown
            </TabsTrigger>
            <TabsTrigger value="preview">
              <Eye className="size-4" /> Preview
            </TabsTrigger>
          </TabsList>
          {/* Both editors are mounted from the same `body` state, so switching
              tabs is a pure view change — nothing to sync, nothing to lose. */}
          <TabsContent value="write" className="mt-3">
            <RichTextEditor
              value={body}
              onChange={setBody}
              variant="full"
              disabled={!canWrite}
              ariaLabel="Article body"
              placeholder="Write your article…"
              className="min-h-[24rem]"
            />
          </TabsContent>
          <TabsContent value="markdown" className="mt-3">
            <Textarea
              aria-label="Article markdown"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={!canWrite}
              rows={18}
              className="font-mono text-sm"
              placeholder="Write your article in markdown…"
            />
          </TabsContent>
          <TabsContent value="preview" className="mt-3">
            <div className="min-h-[24rem] rounded-md border p-4">
              {body.trim() ? (
                <Markdown content={body} />
              ) : (
                <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
