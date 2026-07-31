import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import { useUpdateWorkspace, useWorkspace } from '../hooks'

export function WorkspacePanel() {
  const canManage = useHasPerm('workspace:manage')
  const workspace = useWorkspace()
  const update = useUpdateWorkspace()

  const [name, setName] = useState('')
  const [logoUrl, setLogoUrl] = useState('')

  const data = workspace.data
  useEffect(() => {
    if (!data) return
    setName(data.name)
    setLogoUrl(data.logo_url ?? '')
  }, [data])

  if (workspace.isLoading) return <Skeleton className="h-64 w-full max-w-xl" />

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle className="text-sm">Workspace</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="ws-name">Name</Label>
          <Input
            id="ws-name"
            value={name}
            disabled={!canManage}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="ws-slug">Slug</Label>
          <Input id="ws-slug" value={data?.slug ?? ''} disabled />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="ws-logo">Logo URL</Label>
          <Input
            id="ws-logo"
            placeholder="https://…/logo.png"
            value={logoUrl}
            disabled={!canManage}
            onChange={(e) => setLogoUrl(e.target.value)}
          />
        </div>
        {canManage ? (
          <div>
            <Button
              onClick={() => update.mutate({ name: name.trim(), logo_url: logoUrl.trim() || null })}
              disabled={!name.trim() || update.isPending}
            >
              {update.isPending ? 'Saving…' : 'Save changes'}
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            You need the “Manage workspace” permission to edit these settings.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
