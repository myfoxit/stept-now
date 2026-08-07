import { zodResolver } from '@hookform/resolvers/zod'
import { AlertCircle } from 'lucide-react'
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

const schema = z.object({
  email: z.string().email('Enter a valid email'),
  password: z.string().min(1, 'Password is required'),
})

type FormValues = z.infer<typeof schema>

/** Codes the social-login callback can bounce back with (?error=…). */
const OAUTH_ERRORS: Record<string, string> = {
  oauth_denied: 'Sign-in was cancelled at the provider. You can try again.',
  email_unverified:
    "Your email address isn't verified with that provider. Verify it there first, or sign up with email and password.",
  oauth_failed: "Social sign-in didn't complete. Try again, or log in with your password.",
}

export function Component() {
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }
  const [params] = useSearchParams()
  const oauthError = params.get('error') ? OAUTH_ERRORS[params.get('error') as string] : undefined
  const { setSession } = useAuthStore()
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    try {
      const token = await authApi.login(values)
      adoptAccessToken(token.access_token, token.expires_in)
      const me = await authApi.me()
      setSession(me.user, me.memberships)
      navigate(location.state?.from ?? '/', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Login failed')
    }
  }

  return (
    <AuthCard
      title="Welcome back"
      subtitle="Log in to your Stept workspace"
      footer={
        <p>
          No account?{' '}
          <Link className="text-brand underline-offset-4 hover:underline" to="/signup">
            Sign up
          </Link>
        </p>
      }
    >
      {oauthError ? (
        <Alert variant="destructive" className="mb-4">
          <AlertCircle />
          <AlertDescription>{oauthError}</AlertDescription>
        </Alert>
      ) : null}
      <SocialLoginButtons next="/" />
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <div className="grid gap-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" placeholder="you@company.com" {...form.register('email')} />
          <FieldError message={form.formState.errors.email?.message} />
        </div>
        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <Link
              to="/forgot-password"
              className="text-xs text-muted-foreground underline-offset-4 hover:underline"
            >
              Forgot password?
            </Link>
          </div>
          <Input id="password" type="password" {...form.register('password')} />
          <FieldError message={form.formState.errors.password?.message} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? 'Logging in…' : 'Log in'}
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
