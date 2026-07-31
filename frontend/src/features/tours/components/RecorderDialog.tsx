import { Check, Copy } from 'lucide-react'
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

import { useRecorderToken } from '../hooks'

export function RecorderDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const recorder = useRecorderToken()
  const { mutate } = recorder
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (open) mutate()
  }, [open, mutate])

  const token = recorder.data?.token

  async function copy() {
    if (!token) return
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* ignore */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect the tour recorder</DialogTitle>
          <DialogDescription>
            Capture steps by clicking through your live app with the Stept Chrome extension.
          </DialogDescription>
        </DialogHeader>

        <ol className="grid gap-3 py-2 text-sm">
          <li className="flex gap-2">
            <span className="font-medium text-muted-foreground">1.</span>
            <span>Install the Stept Tour Recorder from the Chrome Web Store.</span>
          </li>
          <li className="flex gap-2">
            <span className="font-medium text-muted-foreground">2.</span>
            <div className="flex-1">
              <p className="mb-1">Paste this token into the extension (valid for 7 days):</p>
              {recorder.isPending ? (
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Spinner className="size-4" /> Generating…
                </div>
              ) : token ? (
                <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
                  <code className="flex-1 truncate font-mono text-xs" data-testid="recorder-token">
                    {token}
                  </code>
                  <Button variant="ghost" size="icon" className="size-7" aria-label="Copy token" onClick={copy}>
                    {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-destructive">Could not generate a token.</p>
              )}
            </div>
          </li>
          <li className="flex gap-2">
            <span className="font-medium text-muted-foreground">3.</span>
            <span>Record your flow — captured tours appear back here to publish.</span>
          </li>
        </ol>
      </DialogContent>
    </Dialog>
  )
}
