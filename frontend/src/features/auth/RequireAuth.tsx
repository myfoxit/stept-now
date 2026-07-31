import { useEffect, useRef } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'

import { authApi } from '@/features/auth/api'
import { Spinner } from '@/components/ui/spinner'
import { useAuthStore } from '@/stores/auth'

/**
 * Session gate: on first mount tries the refresh cookie → /me. Renders children
 * once bootstrapped; redirects to /login when unauthenticated; pushes users
 * without a workspace into onboarding.
 */
export function RequireAuth() {
  const { user, bootstrapped, memberships, setSession, clear } = useAuthStore()
  const location = useLocation()
  const attempted = useRef(false)

  useEffect(() => {
    if (bootstrapped || attempted.current) return
    attempted.current = true
    authApi
      .me()
      .then((me) => setSession(me.user, me.memberships))
      .catch(() => clear())
  }, [bootstrapped, setSession, clear])

  if (!bootstrapped) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="size-6" />
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  if (memberships.length === 0 && location.pathname !== '/onboarding') {
    return <Navigate to="/onboarding" replace />
  }
  return <Outlet />
}
