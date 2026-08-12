import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { t, tParts } from '@/i18n'

export function Component() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // One sentence, split around the address, so the translation decides where the
  // email sits in it.
  const [beforeEmail, afterEmail] = tParts('auth.if_an_account_exists_for_email', 'email')

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await authApi.requestPasswordReset(email)
      setSent(true)
    } catch {
      toast.error(t('auth.something_went_wrong_try_again'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthCard
      title={t('auth.reset_your_password')}
      subtitle={t('auth.we_ll_email_you_a_reset')}
      footer={
        <Link className="text-brand underline-offset-4 hover:underline" to="/login">
          {t('auth.back_to_login')}
        </Link>
      }
    >
      {sent ? (
        <p className="text-center text-sm text-muted-foreground">
          {beforeEmail}
          <span className="font-medium">{email}</span>
          {afterEmail}
        </p>
      ) : (
        <form className="grid gap-4" onSubmit={submit}>
          <div className="grid gap-2">
            <Label htmlFor="email">{t('common.email')}</Label>
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <Button type="submit" disabled={submitting || !email}>
            {submitting ? t('auth.sending') : t('auth.send_reset_link')}
          </Button>
        </form>
      )}
    </AuthCard>
  )
}

export default Component
