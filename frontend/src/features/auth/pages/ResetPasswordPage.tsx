import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { t } from '@/i18n'

export function Component() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await authApi.confirmPasswordReset(token, password)
      toast.success(t('auth.password_updated_log_in_with_your'))
      navigate('/login', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Reset failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthCard title={t('auth.choose_a_new_password')}>
      <form className="grid gap-4" onSubmit={submit}>
        <div className="grid gap-2">
          <Label htmlFor="password">{t('common.new_password')}</Label>
          <Input
            id="password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={submitting || password.length < 8 || !token}>
          {submitting ? 'Updating…' : 'Update password'}
        </Button>
      </form>
    </AuthCard>
  )
}

export default Component
