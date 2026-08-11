/**
 * CSV import wizard: upload → map columns → run.
 *
 * The upload is stored and parsed server-side, which is what lets us show real
 * headers and sample rows before anything is written. Nothing touches the
 * directory until "Start import".
 */

import { Upload } from 'lucide-react'
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
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import type { ContactImportPreview } from '@/features/contacts/api'
import { useStartImport, useUploadImport } from '@/features/contacts/hooks'
import { t } from '@/i18n'

const SKIP = '__skip__'

const TARGETS = [
  { value: 'name', label: 'Name' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'external_id', label: 'External ID' },
]

const IDENTIFYING = new Set(['email', 'phone', 'external_id'])

export function ImportDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [preview, setPreview] = useState<ContactImportPreview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [customKeys, setCustomKeys] = useState<Record<string, string>>({})
  const upload = useUploadImport()
  const start = useStartImport()

  function reset() {
    setPreview(null)
    setMapping({})
    setCustomKeys({})
  }

  async function onFile(file: File | undefined) {
    if (!file) return
    try {
      const result = await upload.mutateAsync(file)
      setPreview(result)
      setMapping({ ...result.contact_import.mapping } as Record<string, string>)
    } catch {
      /* toast handled in the hook */
    }
  }

  /** Resolve the effective target for a column, folding in a custom attribute key. */
  function resolved(header: string): string {
    const target = mapping[header] ?? ''
    if (target !== 'attributes') return target
    const key = (customKeys[header] ?? '').trim()
    return key ? `attributes.${key}` : ''
  }

  const effective = Object.fromEntries(
    (preview?.headers ?? []).map((header) => [header, resolved(header)])
  )
  const hasIdentity = Object.values(effective).some((target) => IDENTIFYING.has(target))

  async function run() {
    if (!preview) return
    try {
      await start.mutateAsync({
        id: preview.contact_import.id,
        mapping: Object.fromEntries(
          Object.entries(effective).filter(([, target]) => target !== '')
        ),
      })
      onOpenChange(false)
      reset()
    } catch {
      /* toast handled in the hook */
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('contacts.import_contacts')}</DialogTitle>
          <DialogDescription>
            {t('contacts.upload_a_csv_exported_from_your')}
          </DialogDescription>
        </DialogHeader>

        {!preview ? (
          <div className="grid gap-2 py-4">
            <Label htmlFor="import-file">{t('contacts.csv_file')}</Label>
            <Input
              id="import-file"
              type="file"
              accept=".csv,text/csv"
              disabled={upload.isPending}
              onChange={(e) => onFile(e.target.files?.[0])}
            />
          </div>
        ) : (
          <div className="grid max-h-[50vh] gap-3 overflow-y-auto py-2">
            <p className="text-sm text-muted-foreground">
              {preview.contact_import.total_rows} rows · map each column below.
            </p>
            {preview.headers.map((header) => (
              <div key={header} className="flex flex-wrap items-center gap-2">
                <span className="w-40 truncate text-sm font-medium" title={header}>
                  {header}
                </span>
                <Select
                  value={mapping[header] || SKIP}
                  onValueChange={(value) =>
                    setMapping((prev) => ({ ...prev, [header]: value === SKIP ? '' : value }))
                  }
                >
                  <SelectTrigger className="h-8 w-44" aria-label={`Map column ${header}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={SKIP}>{t('contacts.skip')}</SelectItem>
                    {TARGETS.map((target) => (
                      <SelectItem key={target.value} value={target.value}>
                        {target.label}
                      </SelectItem>
                    ))}
                    <SelectItem value="attributes">{t('contacts.custom_attribute')}</SelectItem>
                  </SelectContent>
                </Select>
                {mapping[header] === 'attributes' ? (
                  <Input
                    className="h-8 w-40"
                    placeholder={t('contacts.attribute_key')}
                    aria-label={`Attribute key for ${header}`}
                    value={customKeys[header] ?? ''}
                    onChange={(e) =>
                      setCustomKeys((prev) => ({ ...prev, [header]: e.target.value }))
                    }
                  />
                ) : null}
                <span className="truncate text-xs text-muted-foreground">
                  e.g. {preview.sample_rows[0]?.[header] || '—'}
                </span>
              </div>
            ))}
            {!hasIdentity ? (
              <p className="text-xs text-destructive">
                {t('contacts.map_at_least_one_of_email')}
              </p>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          {preview ? (
            <Button onClick={run} disabled={!hasIdentity || start.isPending}>
              <Upload className="mr-1 size-4" />
              {t('contacts.start_import')}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
