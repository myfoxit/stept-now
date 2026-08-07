/**
 * Typed custom attribute definitions for contacts and conversations.
 *
 * A definition turns a free-form JSON key into something with a label, a type,
 * validation and filter operators. Deleting one removes only the definition —
 * stored values stay put, so re-creating it brings the data back into view.
 */

import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useHasPerm } from '@/stores/auth'
import type { CustomAttribute } from '@/features/settings/api'
import {
  useCreateAttribute,
  useCustomAttributes,
  useDeleteAttribute,
  useUpdateAttribute,
} from '@/features/settings/hooks'

const TYPES = ['text', 'number', 'currency', 'percent', 'link', 'date', 'list', 'checkbox'] as const

const MODELS = [
  { value: 'contact', label: 'Contact' },
  { value: 'conversation', label: 'Conversation' },
] as const

export function AttributesPanel() {
  const canManage = useHasPerm('workspace:manage')
  const [model, setModel] = useState<string>('contact')
  const [editing, setEditing] = useState<CustomAttribute | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: definitions = [], isLoading } = useCustomAttributes(model)
  const remove = useDeleteAttribute()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={model} onValueChange={setModel}>
          <SelectTrigger className="w-40" aria-label="Attribute model">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MODELS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {canManage ? (
          <Button
            className="ml-auto"
            size="sm"
            onClick={() => {
              setEditing(null)
              setDialogOpen(true)
            }}
          >
            <Plus className="mr-1 size-4" />
            New attribute
          </Button>
        ) : null}
      </div>

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : definitions.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No typed attributes yet. Free-form values keep working — a definition just adds a label,
          validation and filter operators.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Key</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {definitions.map((definition) => (
              <TableRow key={definition.id}>
                <TableCell className="font-medium">{definition.display_name}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {definition.key}
                </TableCell>
                <TableCell className="capitalize">{definition.attribute_type}</TableCell>
                <TableCell className="text-right">
                  {canManage ? (
                    <>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Edit ${definition.display_name}`}
                        onClick={() => {
                          setEditing(definition)
                          setDialogOpen(true)
                        }}
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Delete ${definition.display_name}`}
                        onClick={() => remove.mutate(definition.id)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AttributeDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        model={model}
        definition={editing}
      />
    </div>
  )
}

function AttributeDialog({
  open,
  onOpenChange,
  model,
  definition,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  model: string
  definition: CustomAttribute | null
}) {
  const create = useCreateAttribute()
  const update = useUpdateAttribute()
  const [displayName, setDisplayName] = useState('')
  const [key, setKey] = useState('')
  const [type, setType] = useState<string>('text')
  const [options, setOptions] = useState('')
  const [regex, setRegex] = useState('')
  const [cue, setCue] = useState('')

  // Re-seed each time the dialog opens (same pattern as SlaPanel).
  const [seeded, setSeeded] = useState<string | null>(null)
  const seedKey = `${open}:${definition?.id ?? 'new'}`
  if (open && seeded !== seedKey) {
    setSeeded(seedKey)
    setDisplayName(definition?.display_name ?? '')
    setKey(definition?.key ?? '')
    setType(definition?.attribute_type ?? 'text')
    setOptions((definition?.options ?? []).map(String).join(', '))
    setRegex(definition?.regex_pattern ?? '')
    setCue(definition?.regex_cue ?? '')
  }

  const parsedOptions = options
    .split(',')
    .map((option) => option.trim())
    .filter(Boolean)
  const needsOptions = type === 'list' && parsedOptions.length === 0
  const canSave = displayName.trim() && (definition || key.trim()) && !needsOptions

  async function save() {
    const body = {
      display_name: displayName.trim(),
      attribute_type: type,
      options: parsedOptions,
      regex_pattern: regex.trim() || null,
      regex_cue: cue.trim() || null,
    }
    try {
      if (definition) {
        await update.mutateAsync({ id: definition.id, body })
      } else {
        await create.mutateAsync({
          ...body,
          attribute_model: model,
          key: key.trim(),
          ord: 0,
          shown_on_front: true,
        })
      }
      onOpenChange(false)
    } catch {
      /* toast handled in the hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{definition ? 'Edit attribute' : 'New attribute'}</DialogTitle>
          <DialogDescription>
            {definition
              ? 'The key and the model are fixed — changing either would orphan every stored value.'
              : 'Values live on the record; this describes how they are shown and validated.'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="attr-name">Display name</Label>
            <Input
              id="attr-name"
              placeholder="e.g. Monthly revenue"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="attr-key">Key</Label>
            <Input
              id="attr-key"
              placeholder="mrr"
              value={key}
              disabled={!!definition}
              onChange={(e) => setKey(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Lowercase letters, digits and underscores.
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="attr-type">Type</Label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger id="attr-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TYPES.map((option) => (
                  <SelectItem key={option} value={option} className="capitalize">
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {type === 'list' ? (
            <div className="grid gap-1.5">
              <Label htmlFor="attr-options">Options</Label>
              <Input
                id="attr-options"
                placeholder="free, pro, enterprise"
                value={options}
                onChange={(e) => setOptions(e.target.value)}
              />
              {needsOptions ? (
                <p className="text-xs text-destructive">A list attribute needs an option.</p>
              ) : null}
            </div>
          ) : null}
          {['text', 'link'].includes(type) ? (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="attr-regex">Validation pattern (optional)</Label>
                <Input
                  id="attr-regex"
                  placeholder="^ACME-\\d+$"
                  value={regex}
                  onChange={(e) => setRegex(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="attr-cue">Hint shown when it fails</Label>
                <Input
                  id="attr-cue"
                  placeholder="Use the format ACME-123"
                  value={cue}
                  onChange={(e) => setCue(e.target.value)}
                />
              </div>
            </>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || create.isPending || update.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
