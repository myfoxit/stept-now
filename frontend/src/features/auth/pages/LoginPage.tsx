import { zodResolver } from '@hookform/resolvers/zod'
import { AlertCircle } from 'lucide-react'
import { useMemo } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'

import { ApiError, adoptAccessToken } from '@/api/client'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { SocialLoginButtons } from '@/features/auth/components/SocialLoginButtons'
import { authApi } from '@/features/auth/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

// Built during render, not once at import: a module constant would freeze the
// English messages before the user's language is known.
function makeSchema() {
  return z.object({
    email: z.string().email(t('auth.enter_a_valid_email')),
    password: z.string().min(1, t('auth.password_is_required')),
  })
}

type FormValues = z.infer<ReturnType<typeof makeSchema>>

/** Codes the social-login callback can bounce back with (?error=…). */
const OAUTH_ERROR_KEYS: Record<string, string> = {
  oauth_denied: 'auth.sign_in_was_cancelled_at_the',
  email_unverified: 'auth.your_email_address_isn_t_verified',
  oauth_failed: 'auth.social_sign_in_didn_t_complete',
}

export function Component() {
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }
  const [params] = useSearchParams()
  const oauthErrorKey = params.get('error')
    ? OAUTH_ERROR_KEYS[params.get('error') as string]
    : undefined
  const { setSession } = useAuthStore()
  const schema = useMemo(makeSchema, [])
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    try {
      const token = await authApi.login(values)
      adoptAccessToken(token.access_token, token.expires_in)
      const me = await authApi.me()
      setSession(me.user, me.memberships)
      navigate(location.state?.from ?? '/', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t('auth.login_failed'))
    }
  }

  return (
    <AuthCard
      title={t('auth.welcome_back')}
      subtitle={t('auth.log_in_to_your_stept_workspace')}
      footer={
        <p>
          {t('auth.no_account')}{' '}
          <Link className="text-brand underline-offset-4 hover:underline" to="/signup">
            {t('auth.sign_up')}
          </Link>
        </p>
      }
    >
      {oauthErrorKey ? (
        <Alert variant="destructive" className="mb-4">
          <AlertCircle />
          <AlertDescription>{t(oauthErrorKey)}</AlertDescription>
        </Alert>
      ) : null}
      <SocialLoginButtons next="/" />
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <div className="grid gap-2">
          <Label htmlFor="email">{t('common.email')}</Label>
          <Input id="email" type="email" placeholder={t('auth.you_company_com')} {...form.register('email')} />
          <FieldError message={form.formState.errors.email?.message} />
        </div>
        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">{t('auth.password')}</Label>
            <Link
              to="/forgot-password"
              className="text-xs text-muted-foreground underline-offset-4 hover:underline"
            >
              {t('auth.forgot_password')}
            </Link>
          </div>
          <Input id="password" type="password" {...form.register('password')} />
          <FieldError message={form.formState.errors.password?.message} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? t('auth.logging_in') : t('auth.log_in')}
        </Button>
      </form>
    </AuthCard>
  )
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return <p className="text-xs text-destructive">{message}</p>
}

export default Component
