import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { fullDateTime } from '@/lib/format'

import type { Webhook } from '../api'
import { useWebhookDeliveries } from '../hooks'
import { t } from '@/i18n'

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'success') return 'default'
  if (status === 'failed' || status === 'error') return 'destructive'
  return 'secondary'
}

export function DeliveriesDialog({
  webhook,
  onOpenChange,
}: {
  webhook: Webhook | null
  onOpenChange: (open: boolean) => void
}) {
  const deliveries = useWebhookDeliveries(webhook?.id ?? null)

  return (
    <Dialog open={webhook !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t('automation.recent_deliveries')}</DialogTitle>
          <DialogDescription className="truncate font-mono text-xs">{webhook?.url}</DialogDescription>
        </DialogHeader>

        {deliveries.isLoading ? (
          <div className="grid gap-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : !deliveries.data || deliveries.data.items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {t('automation.no_deliveries_yet_send_a_test')}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('common.event')}</TableHead>
                  <TableHead>{t('common.status')}</TableHead>
                  <TableHead className="text-right">{t('automation.code')}</TableHead>
                  <TableHead className="text-right">{t('automation.attempts')}</TableHead>
                  <TableHead>{t('common.when')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deliveries.data.items.map((delivery) => (
                  <TableRow key={delivery.id}>
                    <TableCell className="font-mono text-xs">{delivery.event_name}</TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(delivery.status)}>{delivery.status}</Badge>
                      {delivery.error ? (
                        <span className="ml-2 text-xs text-destructive">{delivery.error}</span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {delivery.response_code ?? '—'}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{delivery.attempts}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {fullDateTime(delivery.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
