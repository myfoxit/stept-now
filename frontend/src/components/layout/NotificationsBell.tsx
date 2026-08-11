import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useCallback } from 'react'
import { useNavigate } from 'react-router'

import { api, ws } from '@/api/client'
import { useRealtime } from '@/api/ws'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { timeAgo } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

interface Notification {
  id: string
  type: string
  title: string
  body?: string | null
  link?: string | null
  read_at?: string | null
  created_at: string
}

export function NotificationsBell() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)

  const { data: notifications = [] } = useQuery({
    queryKey: ['notifications', workspaceId],
    queryFn: () => api.get<Notification[]>(ws('/notifications')),
    enabled: !!workspaceId,
  })

  useRealtime(
    'notification.created',
    useCallback(() => {
      queryClient.invalidateQueries({ queryKey: ['notifications', workspaceId] })
    }, [queryClient, workspaceId])
  )

  const markAll = useMutation({
    mutationFn: () => api.post(ws('/notifications/read-all')),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['notifications', workspaceId] }),
  })

  const unread = notifications.filter((n) => !n.read_at)

  function open(notification: Notification) {
    api.post(ws(`/notifications/${notification.id}/read`)).finally(() => {
      queryClient.invalidateQueries({ queryKey: ['notifications', workspaceId] })
    })
    if (notification.link) navigate(notification.link)
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative shrink-0" aria-label={t('common.notifications')}>
          <Bell className="size-4" />
          {unread.length > 0 ? (
            <span className="absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center rounded-full bg-brand text-[10px] font-semibold text-brand-foreground">
              {unread.length > 9 ? '9+' : unread.length}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-semibold">{t('common.notifications')}</span>
          {unread.length > 0 ? (
            <Button variant="ghost" size="sm" onClick={() => markAll.mutate()}>
              {t('common.mark_all_read')}
            </Button>
          ) : null}
        </div>
        <div className="max-h-80 overflow-y-auto scrollbar-thin">
          {notifications.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-muted-foreground">
              Nothing here yet — you're all caught up.
            </p>
          ) : (
            notifications.map((notification) => (
              <button
                key={notification.id}
                onClick={() => open(notification)}
                className="flex w-full flex-col gap-0.5 border-b px-3 py-2 text-left last:border-0 hover:bg-accent"
              >
                <span className="flex items-center gap-2 text-sm">
                  {!notification.read_at ? (
                    <span className="size-1.5 shrink-0 rounded-full bg-brand" />
                  ) : null}
                  <span className="truncate font-medium">{notification.title}</span>
                </span>
                {notification.body ? (
                  <span className="line-clamp-2 text-xs text-muted-foreground">
                    {notification.body}
                  </span>
                ) : null}
                <span className="text-[11px] text-muted-foreground">
                  {timeAgo(notification.created_at)} ago
                </span>
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
