import { Check, Link2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'

import { usePreviewToken } from '../hooks'
import { previewLink } from '../lib'
import { t } from '@/i18n'

/**
 * Mints a one-hour, single-tour preview token and hands the author the hash
 * link that makes the widget play it on their own site — drafts included.
 */
export function PreviewLinkCard({
  tourId,
  disabled = false,
}: {
  tourId: string
  disabled?: boolean
}) {
  const mint = usePreviewToken()
  const [link, setLink] = useState('')
  const [copied, setCopied] = useState(false)

  async function generate() {
    try {
      const { token } = await mint.mutateAsync(tourId)
      const url = previewLink(token)
      setLink(url)
      try {
        await navigator.clipboard.writeText(url)
        setCopied(true)
        toast.success(t('tours.preview_link_copied'))
        setTimeout(() => setCopied(false), 2000)
      } catch {
        toast.message(t('tours.preview_link_ready_copy_it_below'))
      }
    } catch {
      /* toast handled in the hook */
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('tours.preview_on_your_site')}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-fit"
          disabled={disabled || mint.isPending}
          onClick={generate}
        >
          {mint.isPending ? (
            <Spinner className="size-4" />
          ) : copied ? (
            <Check className="size-4" />
          ) : (
            <Link2 className="size-4" />
          )}
          {copied ? 'Copied' : 'Copy preview link'}
        </Button>
        {link ? (
          <code
            className="block overflow-x-auto rounded-md bg-muted px-2 py-1.5 font-mono text-[11px]"
            data-testid="preview-link"
          >
            {link}
          </code>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {t('tours.append_that')} <code className="font-mono">#stept-preview=…</code> hash to any page of your
          site that loads the Stept widget: the tour plays immediately, even as a draft. The link
          works for one hour.
        </p>
      </CardContent>
    </Card>
  )
}
