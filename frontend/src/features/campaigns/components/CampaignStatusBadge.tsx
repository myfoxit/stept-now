import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'

export function CampaignStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'active':
      return <Badge>Active</Badge>
    case 'processing':
      return (
        <Badge variant="outline">
          <Spinner className="size-3" /> Processing
        </Badge>
      )
    case 'completed':
      return (
        <Badge variant="outline" className="text-muted-foreground">
          Completed
        </Badge>
      )
    default:
      return <Badge variant="secondary">Draft</Badge>
  }
}
