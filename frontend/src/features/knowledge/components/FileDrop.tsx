/**
 * Drag & drop file picker shared by the source-creation and add-document
 * flows. Purely presentational — the caller owns the file list and decides
 * what uploading them means.
 */

import { FileText, Loader2, X } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { formatBytes } from '@/lib/format'
import { cn } from '@/lib/utils'

import { DOCUMENT_ACCEPT } from '../api'

/** Per-file lifecycle while a batch upload runs. */
export type UploadState = 'queued' | 'uploading' | 'indexed' | 'failed'

export interface FileStatus {
  state: UploadState
  error?: string | null
}

const STATE_LABEL: Record<UploadState, string> = {
  queued: 'Queued',
  uploading: 'Uploading',
  indexed: 'Indexed',
  failed: 'Failed',
}

export function FileDrop({
  files,
  onChange,
  statuses,
  disabled = false,
  max,
}: {
  files: File[]
  onChange: (files: File[]) => void
  /** Optional per-file status keyed by list index. */
  statuses?: Record<number, FileStatus>
  disabled?: boolean
  /** Cap enforced client-side (the batch endpoint accepts 20). */
  max?: number
}) {
  const [dragging, setDragging] = useState(false)

  const add = (incoming: File[]) => {
    const next = [...files, ...incoming]
    onChange(max ? next.slice(0, max) : next)
  }

  return (
    <div className="grid gap-2">
      <label
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!disabled) add(Array.from(e.dataTransfer.files))
        }}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center text-sm transition-colors hover:bg-accent/50',
          dragging && 'border-primary bg-accent/50',
          disabled && 'pointer-events-none opacity-60'
        )}
      >
        <FileText className="size-6 text-muted-foreground" />
        <span className="font-medium">Drop files or click to browse</span>
        <span className="text-xs text-muted-foreground">
          PDF, DOCX, HTML, Markdown, CSV or TXT{max ? ` · up to ${max} at a time` : ''}
        </span>
        <input
          type="file"
          multiple
          accept={DOCUMENT_ACCEPT}
          disabled={disabled}
          className="sr-only"
          aria-label="Upload files"
          onChange={(e) => {
            add(Array.from(e.target.files ?? []))
            e.target.value = ''
          }}
        />
      </label>
      {files.length > 0 ? (
        <ul className="grid gap-1">
          {files.map((file, i) => {
            const status = statuses?.[i]
            return (
              <li
                key={`${file.name}-${i}`}
                className="flex items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-sm"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{file.name}</span>
                  {status?.error ? (
                    <span className="block truncate text-xs text-destructive">{status.error}</span>
                  ) : null}
                </span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  {formatBytes(file.size)}
                  {status ? <StatusBadge status={status} /> : null}
                  {disabled ? null : (
                    <button
                      type="button"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => onChange(files.filter((_, idx) => idx !== i))}
                      className="rounded p-0.5 hover:bg-muted"
                    >
                      <X className="size-3.5" />
                    </button>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}

function StatusBadge({ status }: { status: FileStatus }) {
  const label = STATE_LABEL[status.state]
  if (status.state === 'uploading') {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="size-3 animate-spin" /> {label}
      </Badge>
    )
  }
  return (
    <Badge
      variant={
        status.state === 'failed' ? 'destructive' : status.state === 'indexed' ? 'default' : 'outline'
      }
    >
      {label}
    </Badge>
  )
}
