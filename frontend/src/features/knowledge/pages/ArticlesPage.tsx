import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, FileText, MoreVertical, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys, type Collection } from '../api'
import { ArticleEditor } from '../components/ArticleEditor'
import { CollectionDialog } from '../components/CollectionDialog'
import { KnowledgeNav, PageHeader, PageShell } from '../components/shell'
import { useArticles, useCollections } from '../hooks'

export function Component() {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const canWrite = useHasPerm('knowledge:write')
  const collections = useCollections()
  const [collectionId, setCollectionId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('all')
  const [selectedArticle, setSelectedArticle] = useState<string | null>(null)
  const [collectionDialog, setCollectionDialog] = useState<{ open: boolean; edit?: Collection | null }>(
    { open: false }
  )

  const articles = useArticles(collectionId, status === 'all' ? null : status)

  const createArticle = useMutation({
    mutationFn: () =>
      knowledgeApi.createArticle({ title: 'Untitled article', collection_id: collectionId }),
    onSuccess: (article) => {
      queryClient.invalidateQueries({ queryKey: ['knowledge', workspaceId, 'articles'] })
      setSelectedArticle(article.id)
      toast.success('Draft created')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not create article'),
  })

  const deleteCollection = useMutation({
    mutationFn: (id: string) => knowledgeApi.deleteCollection(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.collections(workspaceId) })
      queryClient.invalidateQueries({ queryKey: ['knowledge', workspaceId, 'articles'] })
      if (collectionId === id) setCollectionId(null)
      toast.success('Collection deleted')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  const items = articles.data?.items ?? []

  return (
    <PageShell>
      <PageHeader title="Help center" description="Author and publish customer-facing articles" />
      <KnowledgeNav />
      <div className="flex min-h-0 flex-1">
        {/* Collections rail */}
        <aside className="flex w-56 shrink-0 flex-col border-r">
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            <button
              onClick={() => setCollectionId(null)}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent',
                collectionId === null && 'bg-accent font-medium'
              )}
            >
              <BookOpen className="size-4" /> All articles
            </button>
            {collections.isLoading ? (
              <div className="flex justify-center py-4">
                <Spinner className="size-4" />
              </div>
            ) : (
              (collections.data ?? []).map((collection) => (
                <div
                  key={collection.id}
                  className={cn(
                    'group flex items-center gap-1 rounded-md pr-1 hover:bg-accent',
                    collectionId === collection.id && 'bg-accent'
                  )}
                >
                  <button
                    onClick={() => setCollectionId(collection.id)}
                    className={cn(
                      'flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm',
                      collectionId === collection.id && 'font-medium'
                    )}
                  >
                    <span>{collection.icon ?? '📄'}</span>
                    <span className="truncate">{collection.name}</span>
                  </button>
                  {canWrite ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-6 opacity-0 group-hover:opacity-100"
                          aria-label={`Actions for ${collection.name}`}
                        >
                          <MoreVertical className="size-3.5" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => setCollectionDialog({ open: true, edit: collection })}
                        >
                          <Pencil className="size-4" /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() => deleteCollection.mutate(collection.id)}
                        >
                          <Trash2 className="size-4" /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                </div>
              ))
            )}
          </div>
          {canWrite ? (
            <div className="border-t p-2">
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                onClick={() => setCollectionDialog({ open: true, edit: null })}
              >
                <Plus className="size-4" /> New collection
              </Button>
            </div>
          ) : null}
        </aside>

        {/* Article list */}
        <div className="flex w-80 shrink-0 flex-col border-r">
          <div className="flex items-center gap-2 border-b p-2">
            <NativeSelect
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Filter by status"
              size="sm"
              className="flex-1"
            >
              <NativeSelectOption value="all">All statuses</NativeSelectOption>
              <NativeSelectOption value="draft">Draft</NativeSelectOption>
              <NativeSelectOption value="published">Published</NativeSelectOption>
            </NativeSelect>
            {canWrite ? (
              <Button
                size="sm"
                onClick={() => createArticle.mutate()}
                disabled={createArticle.isPending}
              >
                <Plus className="size-4" /> New
              </Button>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {articles.isLoading ? (
              <div className="flex justify-center py-6">
                <Spinner className="size-4" />
              </div>
            ) : items.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">No articles here yet.</p>
            ) : (
              <ul>
                {items.map((article) => (
                  <li key={article.id}>
                    <button
                      onClick={() => setSelectedArticle(article.id)}
                      className={cn(
                        'flex w-full flex-col gap-1 border-b px-3 py-2.5 text-left hover:bg-accent',
                        selectedArticle === article.id && 'bg-accent'
                      )}
                    >
                      <span className="truncate text-sm font-medium">{article.title}</span>
                      <Badge
                        variant={article.status === 'published' ? 'default' : 'secondary'}
                        className="w-fit text-[10px]"
                      >
                        {article.status}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Editor */}
        <div className="min-w-0 flex-1">
          {selectedArticle ? (
            <ArticleEditor
              key={selectedArticle}
              articleId={selectedArticle}
              collections={collections.data ?? []}
              onDeleted={() => setSelectedArticle(null)}
            />
          ) : (
            <Empty className="h-full">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <FileText />
                </EmptyMedia>
                <EmptyTitle>Select an article</EmptyTitle>
                <EmptyDescription>
                  Choose an article from the list, or create a new draft to start writing.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </div>

      <CollectionDialog
        collection={collectionDialog.edit}
        open={collectionDialog.open}
        onOpenChange={(open) => setCollectionDialog((prev) => ({ ...prev, open }))}
      />
    </PageShell>
  )
}

export default Component
