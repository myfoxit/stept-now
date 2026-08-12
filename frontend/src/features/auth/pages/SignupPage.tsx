import { zodResolver } from '@hookform/resolvers/zod'
import { useMemo } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'

import { ApiError, adoptAccessToken } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { SocialLoginButtons } from '@/features/auth/components/SocialLoginButtons'
import { FieldError } from '@/features/auth/pages/LoginPage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

// Built during render, not once at import: a module constant would freeze the
// English messages before the user's language is known.
function makeSchema() {
  return z.object({
    name: z.string().min(1, t('auth.your_name_is_required')),
    email: z.string().email(t('auth.enter_a_valid_email')),
    password: z.string().min(8, t('auth.at_least_8_characters')),
  })
}

type FormValues = z.infer<ReturnType<typeof makeSchema>>

export function Component() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const inviteToken = params.get('invite')
  const { setSession } = useAuthStore()
  const schema = useMemo(makeSchema, [])
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    try {
      const token = await authApi.signup(values)
      adoptAccessToken(token.access_token, token.expires_in)
      if (inviteToken) {
        await authApi.acceptInvite(inviteToken).catch(() => {
          toast.error(t('auth.could_not_accept_the_invitation_automatically'))
        })
      }
      const me = await authApi.me()
      setSession(me.user, me.memberships)
      navigate(me.memberships.length > 0 ? '/' : '/onboarding', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t('auth.signup_failed'))
    }
  }

  return (
    <AuthCard
      title={t('auth.create_your_account')}
      subtitle={
        inviteToken
          ? t('auth.sign_up_to_join_your_team')
          : t('auth.start_your_open_source_support_hub')
      }
      footer={
        <p>
          {t('auth.already_have_an_account')}{' '}
          <Link className="text-brand underline-offset-4 hover:underline" to="/login">
            {t('auth.log_in')}
          </Link>
        </p>
      }
    >
      <SocialLoginButtons next="/" inviteToken={inviteToken} />
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <div className="grid gap-2">
          <Label htmlFor="name">{t('common.name')}</Label>
          <Input id="name" placeholder={t('auth.ada_lovelace')} {...form.register('name')} />
          <FieldError message={form.formState.errors.name?.message} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="email">{t('common.email')}</Label>
          <Input id="email" type="email" placeholder={t('auth.you_company_com')} {...form.register('email')} />
          <FieldError message={form.formState.errors.email?.message} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="password">{t('auth.password')}</Label>
          <Input id="password" type="password" {...form.register('password')} />
          <FieldError message={form.formState.errors.password?.message} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? t('auth.creating_account') : t('auth.create_account')}
        </Button>
      </form>
    </AuthCard>
  )
}

export default Component
