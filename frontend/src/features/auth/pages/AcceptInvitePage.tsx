import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

export function Component() {
  const [params] = useSearchParams()
  const token = params.get('token')
  const navigate = useNavigate()
  const { user, bootstrapped, setSession, setWorkspace, setBootstrapped } = useAuthStore()
  const [error, setError] = useState<string | null>(null)
  const attempted = useRef(false)

  // Bootstrap session if the user landed here directly.
  useEffect(() => {
    if (bootstrapped || attempted.current) return
    attempted.current = true
    authApi
      .me()
      .then((me) => setSession(me.user, me.memberships))
      .catch(() => setBootstrapped())
  }, [bootstrapped, setSession, setBootstrapped])

  useEffect(() => {
    if (!bootstrapped || !user || !token) return
    authApi
      .acceptInvite(token)
      .then(async (membership) => {
        const me = await authApi.me()
        setSession(me.user, me.memberships)
        setWorkspace(membership.workspace.id)
        toast.success(t('auth.joined_name', { name: membership.workspace.name }))
        navigate('/', { replace: true })
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : t('auth.invitation_invalid'))
      )
  }, [bootstrapped, user, token, navigate, setSession, setWorkspace])

  if (!token) {
    return (
      <AuthCard title={t('auth.invitation_link_invalid')}>
        <p className="text-sm text-muted-foreground">{t('auth.the_invitation_link_is_missing_its')}</p>
      </AuthCard>
    )
  }

  if (!bootstrapped) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-6" />
      </div>
    )
  }

  if (!user) {
    return (
      <AuthCard
        title={t('auth.join_your_team_on_stept')}
        subtitle={t('auth.create_an_account_to_accept_the')}
      >
        <div className="grid gap-3">
          <Button asChild>
            <Link to={`/signup?invite=${encodeURIComponent(token)}`}>
              {t('auth.sign_up_join')}
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to={`/login?from=/accept-invite?token=${encodeURIComponent(token)}`}>
              {t('auth.i_already_have_an_account')}
            </Link>
          </Button>
        </div>
      </AuthCard>
    )
  }

  return (
    <AuthCard title={error ? t('auth.could_not_accept_invitation') : t('auth.joining_workspace')}>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <div className="flex justify-center py-4">
          <Spinner className="size-6" />
        </div>
      )}
    </AuthCard>
  )
}

export default Component
