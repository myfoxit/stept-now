import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export function Component() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await authApi.requestPasswordReset(email)
      setSent(true)
    } catch {
      toast.error('Something went wrong — try again')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      subtitle="We'll email you a reset link"
      footer={
        <Link className="text-brand underline-offset-4 hover:underline" to="/login">
          Back to login
        </Link>
      }
    >
      {sent ? (
        <p className="text-center text-sm text-muted-foreground">
          If an account exists for <span className="font-medium">{email}</span>, a reset link is on
          its way.
        </p>
      ) : (
        <form className="grid gap-4" onSubmit={submit}>
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <Button type="submit" disabled={submitting || !email}>
            {submitting ? 'Sending…' : 'Send reset link'}
          </Button>
        </form>
      )}
    </AuthCard>
  )
}

export default Component
