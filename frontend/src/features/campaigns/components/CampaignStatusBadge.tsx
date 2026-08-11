import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { t } from '@/i18n'

export function CampaignStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'active':
      return <Badge>{t('campaigns.active')}</Badge>
    case 'processing':
      return (
        <Badge variant="outline">
          <Spinner className="size-3" /> {t('campaigns.processing')}
        </Badge>
      )
    case 'completed':
      return (
        <Badge variant="outline" className="text-muted-foreground">
          {t('common.completed_2')}
        </Badge>
      )
    default:
      return <Badge variant="secondary">{t('common.draft')}</Badge>
  }
}
