import { Check, Copy, Download, ExternalLink, Loader2, Puzzle } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Spinner } from '@/components/ui/spinner'

import { useExtensionRelease, useRecorderToken } from '../hooks'
import { t } from '@/i18n'

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return ''
  const kb = bytes / 1024
  return kb < 1024 ? `${Math.round(kb)} KB` : `${(kb / 1024).toFixed(1)} MB`
}

/** A copyable one-liner. `min-w-0` is load-bearing: without it the flex child
 * refuses to shrink and long values (tokens, URLs) escape the dialog. */
function CopyField({
  value,
  label,
  testId,
}: {
  value: string
  label: string
  testId?: string
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked — the value is selectable in the field */
    }
  }

  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" data-testid={testId}>
        {value}
      </code>
      <Button
        variant="ghost"
        size="icon"
        className="size-7 shrink-0"
        aria-label={label}
        onClick={copy}
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </Button>
    </div>
  )
}

function Step({ n, title, children }: { n: number; title: string; children?: React.ReactNode }) {
  return (
    <li className="flex min-w-0 gap-3">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
        {n}
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <p className="font-medium">{title}</p>
        {children}
      </div>
    </li>
  )
}

export function RecorderDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const release = useExtensionRelease()
  const recorder = useRecorderToken()
  const { mutate } = recorder

  useEffect(() => {
    if (open) mutate()
  }, [open, mutate])

  const token = recorder.data?.token
  const info = release.data
  const webStore = info?.web_store_url

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('tours.record_a_tour_with_the_chrome')}</DialogTitle>
          <DialogDescription>
            {t('tours.click_through_your_product_once_the')}
          </DialogDescription>
        </DialogHeader>

        <ol className="grid min-w-0 gap-5 py-2 text-sm">
          <Step n={1} title={t('tours.install_the_recorder')}>
            {release.isPending ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Spinner className="size-4" /> {t('tours.checking_for_a_build')}
              </div>
            ) : webStore ? (
              <Button asChild size="sm">
                <a href={webStore} target="_blank" rel="noreferrer">
                  <Puzzle className="size-4" /> {t('tours.add_to_chrome')}
                  <ExternalLink className="size-3.5 opacity-70" />
                </a>
              </Button>
            ) : info?.available ? (
              <>
                <Button asChild size="sm">
                  <a href={info.download_url ?? undefined} download>
                    <Download className="size-4" /> {t('tours.download_the_extension')}
                  </a>
                </Button>
                <p className="text-xs text-muted-foreground">
                  v{info.version} · {formatSize(info.size_bytes)} · unzip it, then load the folder
                  at <code className="font-mono">{t('tours.chrome_extensions')}</code> with{' '}
                  <span className="font-medium">{t('tours.developer_mode')}</span> on →{' '}
                  <span className="font-medium">{t('tours.load_unpacked')}</span>.
                </p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                No build found on the server. Run{' '}
                <code className="font-mono">{t('tours.pnpm_filter_stept_extension_zip')}</code> and reload
                this dialog.
              </p>
            )}
          </Step>

          <Step n={2} title={t('tours.point_it_at_this_stept')}>
            <p className="text-xs text-muted-foreground">
              {t('tours.open_the_stept_side_panel_then')}
            </p>
            <CopyField
              value={info?.api_base ?? ''}
              label={t('tours.copy_server_address')}
              testId="recorder-api-base"
            />
          </Step>

          <Step n={3} title={t('tours.record')}>
            <p className="text-xs text-muted-foreground">
              {t('tours.go_to_where_your_flow_starts')} <span className="font-medium">{t('tours.start_recording')}</span>{' '}
              in the panel, click through it, then save. The draft appears in Tours.
            </p>
          </Step>
        </ol>

        {/* `min-w-0` here and below: these are grid items, whose default
            `min-width: auto` lets the unbroken JWT widen the whole dialog. */}
        <details className="group min-w-0 border-t pt-3">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
            {t('tours.can_t_sign_in_from_the')}
          </summary>
          <div className="mt-3 min-w-0 space-y-2">
            <p className="text-xs text-muted-foreground">
              {t('tours.paste_this_under')} <span className="font-medium">{t('tours.advanced')}</span> on the extension’s sign
              in screen. It is workspace-scoped and expires in{' '}
              {recorder.data?.expires_days ?? 7} days.
            </p>
            {recorder.isPending ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {t('tours.generating')}
              </div>
            ) : token ? (
              <CopyField value={token} label={t('tours.copy_token')} testId="recorder-token" />
            ) : (
              <p className="text-xs text-destructive">{t('tours.could_not_generate_a_token')}</p>
            )}
          </div>
        </details>
      </DialogContent>
    </Dialog>
  )
}
