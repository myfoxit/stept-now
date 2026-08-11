import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { Button } from '@/components/ui/button'
import { t } from '@/i18n'

// Same base the api client uses (src/api/client.ts). The start endpoint is a
// full-page navigation, not an XHR, so it needs an absolute href when the API
// lives on another origin.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M23.52 12.273c0-.851-.076-1.67-.218-2.455H12v4.642h6.458a5.52 5.52 0 0 1-2.394 3.622v3.011h3.878c2.269-2.088 3.578-5.165 3.578-8.82Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.956-1.075 7.942-2.907l-3.878-3.011c-1.075.72-2.45 1.145-4.064 1.145-3.125 0-5.771-2.111-6.715-4.948H1.276v3.109A11.995 11.995 0 0 0 12 24Z"
      />
      <path
        fill="#FBBC05"
        d="M5.285 14.279A7.213 7.213 0 0 1 4.909 12c0-.79.136-1.56.376-2.279V6.612H1.276A11.995 11.995 0 0 0 0 12c0 1.936.464 3.769 1.276 5.388l4.009-3.109Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.773c1.762 0 3.344.605 4.587 1.794l3.442-3.442C17.951 1.19 15.235 0 12 0 7.31 0 3.253 2.69 1.276 6.612l4.009 3.109C6.229 6.884 8.875 4.773 12 4.773Z"
      />
    </svg>
  )
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" className="size-4 fill-current" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  )
}

/**
 * "Continue with Google/GitHub" buttons + an "or" divider. Renders nothing at
 * all when the instance has no social provider configured, so the email form
 * stands alone. Navigation is a plain <a> — the backend 302s to the provider.
 */
export function SocialLoginButtons({
  next = '/',
  inviteToken,
}: {
  next?: string
  inviteToken?: string | null
}) {
  const { data } = useQuery({
    queryKey: ['auth', 'oauth-providers'],
    queryFn: () => api.get<{ providers: string[] }>('/api/v1/auth/oauth/providers'),
    staleTime: Infinity,
  })
  const providers = data?.providers ?? []
  if (providers.length === 0) return null

  const startHref = (provider: string) => {
    const params = new URLSearchParams({ next })
    if (inviteToken) params.set('invite', inviteToken)
    return `${API_BASE}/api/v1/auth/oauth/${provider}/start?${params.toString()}`
  }

  return (
    <div className="mb-4 grid gap-4">
      <div className="grid gap-2">
        {providers.includes('google') && (
          <Button variant="outline" asChild>
            <a href={startHref('google')}>
              <GoogleMark />
              {t('auth.continue_with_google')}
            </a>
          </Button>
        )}
        {providers.includes('github') && (
          <Button variant="outline" asChild>
            <a href={startHref('github')}>
              <GitHubMark />
              {t('auth.continue_with_github')}
            </a>
          </Button>
        )}
      </div>
      <div className="relative">
        <div aria-hidden="true" className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-card px-2 text-muted-foreground">{t('auth.or')}</span>
        </div>
      </div>
    </div>
  )
}
