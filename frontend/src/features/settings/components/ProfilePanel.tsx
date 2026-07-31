import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useAuthStore } from '@/stores/auth'

import { useChangePassword, useUpdateProfile } from '../hooks'

export function ProfilePanel() {
  const user = useAuthStore((s) => s.user)
  const updateProfile = useUpdateProfile()
  const changePassword = useChangePassword()

  const [name, setName] = useState(user?.name ?? '')
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')

  const passwordError =
    next.length > 0 && next.length < 8
      ? 'New password must be at least 8 characters'
      : confirm.length > 0 && next !== confirm
        ? 'Passwords do not match'
        : null
  const canChangePassword =
    current.length > 0 && next.length >= 8 && next === confirm && !changePassword.isPending

  async function submitPassword() {
    if (!canChangePassword) return
    await changePassword.mutateAsync({ current_password: current, new_password: next })
    setCurrent('')
    setNext('')
    setConfirm('')
  }

  return (
    <div className="grid max-w-xl gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Your profile</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="profile-email">Email</Label>
            <Input id="profile-email" value={user?.email ?? ''} disabled />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="profile-name">Name</Label>
            <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Button
              onClick={() => updateProfile.mutate({ name: name.trim() })}
              disabled={!name.trim() || name.trim() === user?.name || updateProfile.isPending}
            >
              {updateProfile.isPending ? 'Saving…' : 'Save profile'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Change password</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="pw-current">Current password</Label>
            <Input
              id="pw-current"
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <Separator />
          <div className="grid gap-1.5">
            <Label htmlFor="pw-new">New password</Label>
            <Input
              id="pw-new"
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="pw-confirm">Confirm new password</Label>
            <Input
              id="pw-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
          {passwordError ? <p className="text-xs text-destructive">{passwordError}</p> : null}
          <div>
            <Button onClick={submitPassword} disabled={!canChangePassword}>
              {changePassword.isPending ? 'Updating…' : 'Update password'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
