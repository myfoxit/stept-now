import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'

import { ApiError } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { AuthCard } from '@/features/auth/components/AuthCard'
import { FieldError } from '@/features/auth/pages/LoginPage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

const schema = z.object({ name: z.string().min(1, 'Workspace name is required') })

type FormValues = z.infer<typeof schema>

export function Component() {
  const navigate = useNavigate()
  const { setSession, setWorkspace } = useAuthStore()
  const form = useForm<FormValues>({ resolver: zodResolver(schema) })

  async function onSubmit(values: FormValues) {
    try {
      const workspace = await authApi.createWorkspace(values.name)
      const me = await authApi.me()
      setSession(me.user, me.memberships)
      setWorkspace(workspace.id)
      toast.success(t('auth.workspace_created_welcome_to_stept'))
      navigate('/', { replace: true })
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Could not create workspace')
    }
  }

  return (
    <AuthCard title={t('auth.create_your_workspace')} subtitle="A home for your team's conversations">
      <form className="grid gap-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <div className="grid gap-2">
          <Label htmlFor="name">{t('auth.workspace_name')}</Label>
          <Input id="name" placeholder={t('auth.acme_inc')} {...form.register('name')} />
          <FieldError message={form.formState.errors.name?.message} />
        </div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? 'Creating…' : 'Create workspace'}
        </Button>
      </form>
    </AuthCard>
  )
}

export default Component
