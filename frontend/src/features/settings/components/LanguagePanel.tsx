import { useState } from 'react'
import { toast } from 'sonner'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { LOCALES, t } from '@/i18n'
import { applyUserLocale } from '@/i18n/bootstrap'
import { useTranslation } from '@/i18n/react'
import { useAuthStore } from '@/stores/auth'

import { useUpdateProfile } from '../hooks'

/** "Follow my browser" — distinct from having chosen English. */
const SYSTEM = '__system__'

export function LanguagePanel() {
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const updateProfile = useUpdateProfile()
  // Subscribes this panel to the active language, so its own labels change the
  // instant a new one is picked rather than on the next navigation.
  useTranslation()
  const [saving, setSaving] = useState(false)

  const value = user?.locale ?? SYSTEM

  async function choose(next: string) {
    const chosen = next === SYSTEM ? null : next
    setSaving(true)
    // Apply first, then persist. Waiting on the round trip would leave the
    // person looking at the old language while the request is in flight, which
    // reads as the setting not having worked.
    await applyUserLocale(chosen)
    setUser({ ...user!, locale: chosen })
    try {
      await updateProfile.mutateAsync({ locale: chosen ?? '' })
    } catch {
      toast.error(t('settings.language_failed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('settings.language')}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <p className="text-sm text-muted-foreground">{t('settings.language_description')}</p>
        <div className="grid max-w-xs gap-1.5">
          <Label htmlFor="locale-select">{t('settings.language')}</Label>
          <Select value={value} onValueChange={(next) => void choose(next)} disabled={saving}>
            <SelectTrigger id="locale-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SYSTEM}>{t('settings.language_system')}</SelectItem>
              {LOCALES.map((entry) => (
                <SelectItem key={entry.code} value={entry.code}>
                  {/* The endonym leads: a picker that offers "German" to
                      someone who only reads German is a picker they cannot
                      use. The English name follows for everyone else. */}
                  {entry.nativeName}
                  {entry.nativeName === entry.englishName ? null : (
                    <span className="ms-2 text-muted-foreground">{entry.englishName}</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  )
}
