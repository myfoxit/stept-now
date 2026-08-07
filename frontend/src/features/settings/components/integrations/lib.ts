/** Pure helpers for the integrations catalog UI (grouping, state chips, icons). */

import {
  Blocks,
  BookOpen,
  FolderOpen,
  LifeBuoy,
  Mail,
  MessageSquare,
  NotebookText,
  type LucideIcon,
} from 'lucide-react'

import type { IntegrationConnection, IntegrationProvider, ProviderCategory } from '../../api'

/** Fixed section order for the catalog page. */
export const CATEGORY_ORDER: ProviderCategory[] = ['email', 'knowledge', 'channel', 'app']

export const CATEGORY_LABELS: Record<ProviderCategory, string> = {
  email: 'Email',
  knowledge: 'Knowledge',
  channel: 'Channels',
  app: 'Apps',
}

/** Brand-neutral glyph per provider; category icon as the fallback. */
const PROVIDER_ICONS: Record<string, LucideIcon> = {
  google: Mail,
  microsoft: Mail,
  slack: MessageSquare,
  notion: NotebookText,
  confluence: BookOpen,
  zendesk: LifeBuoy,
  gdrive: FolderOpen,
}

const CATEGORY_ICONS: Record<ProviderCategory, LucideIcon> = {
  email: Mail,
  knowledge: BookOpen,
  channel: MessageSquare,
  app: Blocks,
}

export function providerIcon(provider: IntegrationProvider): LucideIcon {
  return PROVIDER_ICONS[provider.id] ?? CATEGORY_ICONS[provider.category] ?? Blocks
}

/** Connections that still count as "connected" for the state chip (revoked ones don't). */
export function activeConnections(provider: IntegrationProvider): IntegrationConnection[] {
  return provider.connections.filter((c) => c.status !== 'revoked')
}

export type ProviderState = 'connected' | 'needs_setup' | 'not_connected'

export function providerState(provider: IntegrationProvider): ProviderState {
  if (activeConnections(provider).length > 0) return 'connected'
  if (provider.auth === 'oauth2' && !provider.configured) return 'needs_setup'
  return 'not_connected'
}

/** "signing_secret" → "Signing secret" for credential form labels. */
export function fieldLabel(field: string): string {
  const words = field.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Human line for the ?error= safe codes the OAuth callback redirects with. */
export function oauthErrorMessage(code: string): string {
  const known: Record<string, string> = {
    integration_not_configured: 'This integration has no app credentials configured.',
    access_denied: 'You cancelled the authorization at the provider.',
    state_invalid: 'The sign-in link expired — try connecting again.',
    exchange_failed: 'The provider rejected the authorization — try again.',
  }
  return known[code] ?? `Connection failed (${code}).`
}
