import { Trash2, UserPlus } from 'lucide-react'
import { useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { initials } from '@/lib/format'
import { useHasPerm } from '@/stores/auth'

import type { Membership } from '../api'
import {
  useInvitations,
  useInvite,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
  useRoles,
  useUpdateMember,
} from '../hooks'

const BUILTIN_ROLES = [
  { value: 'owner', label: 'Owner' },
  { value: 'admin', label: 'Admin' },
  { value: 'agent', label: 'Agent' },
  { value: 'viewer', label: 'Viewer' },
]

function memberRoleValue(member: Membership): string {
  return member.role === 'custom' && member.custom_role_id
    ? `custom:${member.custom_role_id}`
    : member.role
}

export function MembersPanel() {
  const canManage = useHasPerm('members:manage')
  const members = useMembers()
  const roles = useRoles()
  const invitations = useInvitations()
  const updateMember = useUpdateMember()
  const removeMember = useRemoveMember()
  const invite = useInvite()
  const revoke = useRevokeInvitation()

  const [email, setEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('agent')
  const [removing, setRemoving] = useState<Membership | null>(null)

  const roleOptions = [
    ...BUILTIN_ROLES,
    ...(roles.data ?? []).map((role) => ({ value: `custom:${role.id}`, label: role.name })),
  ]

  function changeRole(member: Membership, value: string) {
    if (value.startsWith('custom:')) {
      updateMember.mutate({ id: member.id, body: { role: 'custom', custom_role_id: value.slice(7) } })
    } else {
      updateMember.mutate({ id: member.id, body: { role: value } })
    }
  }

  function sendInvite() {
    if (!email.trim()) return
    invite.mutate(
      { email: email.trim(), role: inviteRole },
      { onSuccess: () => setEmail('') }
    )
  }

  return (
    <div className="grid gap-6">
      {canManage ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Invite a teammate</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <div className="grid flex-1 gap-1.5">
              <label className="text-xs text-muted-foreground" htmlFor="invite-email">
                Email
              </label>
              <Input
                id="invite-email"
                type="email"
                placeholder="teammate@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="text-xs text-muted-foreground" htmlFor="invite-role">
                Role
              </label>
              <NativeSelect
                id="invite-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
              >
                {BUILTIN_ROLES.map((role) => (
                  <NativeSelectOption key={role.value} value={role.value}>
                    {role.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
            <Button onClick={sendInvite} disabled={!email.trim() || invite.isPending}>
              <UserPlus className="size-4" /> Invite
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Members</CardTitle>
        </CardHeader>
        <CardContent>
          {members.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Member</TableHead>
                    <TableHead>Role</TableHead>
                    {canManage ? <TableHead className="w-10" /> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(members.data ?? []).map((member) => (
                    <TableRow key={member.id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <Avatar className="size-8">
                            <AvatarFallback>{initials(member.user.name)}</AvatarFallback>
                          </Avatar>
                          <div className="min-w-0">
                            <div className="truncate font-medium">{member.user.name}</div>
                            <div className="truncate text-xs text-muted-foreground">
                              {member.user.email}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        {canManage ? (
                          <NativeSelect
                            aria-label={`Role for ${member.user.name}`}
                            size="sm"
                            value={memberRoleValue(member)}
                            disabled={updateMember.isPending}
                            onChange={(e) => changeRole(member, e.target.value)}
                          >
                            {roleOptions.map((role) => (
                              <NativeSelectOption key={role.value} value={role.value}>
                                {role.label}
                              </NativeSelectOption>
                            ))}
                          </NativeSelect>
                        ) : (
                          <Badge variant="secondary" className="capitalize">
                            {member.role}
                          </Badge>
                        )}
                      </TableCell>
                      {canManage ? (
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Remove ${member.user.name}`}
                            onClick={() => setRemoving(member)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </TableCell>
                      ) : null}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {canManage && (invitations.data?.length ?? 0) > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Pending invitations</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2">
            {invitations.data!.map((inv) => (
              <div
                key={inv.id}
                className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-medium">{inv.email}</span>{' '}
                  <Badge variant="outline" className="ml-1 capitalize">
                    {inv.role}
                  </Badge>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(inv.id)}
                >
                  Revoke
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {removing?.user.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              They will lose access to this workspace immediately.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (removing) removeMember.mutate(removing.id)
                setRemoving(null)
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
