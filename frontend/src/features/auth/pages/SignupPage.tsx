import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'

import { ApiError } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { SocialLoginButtons } from '@/features/auth/components/SocialLoginButtons'
import { FieldError } from '@/features/auth/pages/LoginPage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthStore } from '@/stores/auth'

const schema = z.object({
  name: z.string().min(1, 'Your name is required'),
  email: z.string().email('Enter a valid email'),
  password: z.string().min(8, 'At least 8 characters'),
})

type FormValues = z.infer<typeof schema>

export function Component() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const inviteToken = params.get('invite')
  const { setAccessToken, setSession } = useAuthStore()
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    try {
      const token = await authApi.signup(values)
      setAccessToken(token.access_token)
      if (inviteToken) {
        await authApi.acceptInvite(inviteToken).catch(() => {
          toast.error('Could not accept the invitation automatically')
        })
      }
      const me = await authApi.me()
      setSession(me.user, me.memberships)
      navigate(me.memberships.length > 0 ? '/' : '/onboarding', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Signup failed')
    }
  }

  return (
    <AuthCard
      title="Create your account"
      subtitle={inviteToken ? 'Sign up to join your team' : 'Start your open-source support hub'}
      footer={
        <p>
          Already have an account?{' '}
          <Link className="text-primary underline-offset-4 hover:underline" to="/login">
            Log in
          </Link>
        </p>
      }
    >
      <SocialLoginButtons next="/" inviteToken={inviteToken} />
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <div className="grid gap-2">
          <Label htmlFor="name">Name</Label>
          <Input id="name" placeholder="Ada Lovelace" {...form.register('name')} />
          <FieldError message={form.formState.errors.name?.message} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" placeholder="you@company.com" {...form.register('email')} />
          <FieldError message={form.formState.errors.email?.message} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" {...form.register('password')} />
          <FieldError message={form.formState.errors.password?.message} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? 'Creating account…' : 'Create account'}
        </Button>
      </form>
    </AuthCard>
  )
}

export default Component
