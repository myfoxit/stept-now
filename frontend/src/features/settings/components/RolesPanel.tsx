import { Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'

import type { Role } from '../api'
import { useCreateRole, useDeleteRole, usePermissionCatalog, useRoles, useUpdateRole } from '../hooks'
import { PermissionMatrix } from './PermissionMatrix'

function RoleEditorDialog({
  open,
  onOpenChange,
  role,
  allPermissions,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  role: Role | null
  allPermissions: string[]
}) {
  const create = useCreateRole()
  const update = useUpdateRole()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [permissions, setPermissions] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    setName(role?.name ?? '')
    setDescription(role?.description ?? '')
    setPermissions(role?.permissions ?? [])
  }, [open, role])

  const saving = create.isPending || update.isPending

  async function save() {
    const body = { name: name.trim(), description: description.trim() || null, permissions }
    try {
      if (role) await update.mutateAsync({ id: role.id, body })
      else await create.mutateAsync(body)
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{role ? 'Edit role' : 'New role'}</DialogTitle>
          <DialogDescription>
            Custom roles grant an explicit set of permissions to their members.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="role-name">Name</Label>
            <Input
              id="role-name"
              value={name}
              placeholder="e.g. Billing agent"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="role-desc">Description</Label>
            <Textarea
              id="role-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Permissions</Label>
            <PermissionMatrix
              all={allPermissions}
              selected={permissions}
              onChange={setPermissions}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!name.trim() || saving}>
            {saving ? 'Saving…' : role ? 'Save role' : 'Create role'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function RolesPanel() {
  const roles = useRoles()
  const catalog = usePermissionCatalog()
  const remove = useDeleteRole()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Role | null>(null)
  const [deleting, setDeleting] = useState<Role | null>(null)

  const allPermissions = catalog.data?.permissions ?? []

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Built-in roles cover most teams; create custom roles for finer control.
        </p>
        <Button
          size="sm"
          onClick={() => {
            setEditing(null)
            setEditorOpen(true)
          }}
        >
          <Plus className="size-4" /> New role
        </Button>
      </div>

      {catalog.data ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Built-in roles</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(catalog.data.builtin_roles).map(([name, perms]) => (
              <Badge key={name} variant="outline" className="capitalize">
                {name} · {perms.length} perms
              </Badge>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {roles.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !roles.data || roles.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ShieldCheck />
            </EmptyMedia>
            <EmptyTitle>No custom roles</EmptyTitle>
            <EmptyDescription>
              Create a role to grant a tailored set of permissions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              onClick={() => {
                setEditing(null)
                setEditorOpen(true)
              }}
            >
              <Plus className="size-4" /> Create a role
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {roles.data.map((role) => (
            <li key={role.id}>
              <Card className="flex flex-row items-center gap-4 p-4">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{role.name}</div>
                  {role.description ? (
                    <p className="text-xs text-muted-foreground">{role.description}</p>
                  ) : null}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {role.permissions.length} permission{role.permissions.length === 1 ? '' : 's'}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Edit ${role.name}`}
                  onClick={() => {
                    setEditing(role)
                    setEditorOpen(true)
                  }}
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${role.name}`}
                  onClick={() => setDeleting(role)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <RoleEditorDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        role={editing}
        allPermissions={allPermissions}
      />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Members with this role will fall back to no permissions until reassigned.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
