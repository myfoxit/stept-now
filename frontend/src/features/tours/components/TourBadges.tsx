import { Megaphone, PanelTop, Route, Wand2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'

import { kindLabel } from '../lib'

const KIND_ICONS: Record<string, typeof Route> = {
  flow: Route,
  banner: PanelTop,
  announcement: Megaphone,
}

/** Experience kind — flow / banner / announcement all run on the tour engine. */
export function TourKindBadge({ kind }: { kind: string }) {
  const Icon = KIND_ICONS[kind] ?? Route
  return (
    <Badge variant="outline" className="gap-1">
      <Icon aria-hidden />
      {kindLabel(kind)}
    </Badge>
  )
}

/** Only "driven" is worth calling out — guided is the default everyone expects. */
export function TourModeBadge({ mode }: { mode: string | null | undefined }) {
  if (mode !== 'driven') return null
  return (
    <Badge variant="secondary" className="gap-1">
      <Wand2 aria-hidden />
      Do it for me
    </Badge>
  )
}
