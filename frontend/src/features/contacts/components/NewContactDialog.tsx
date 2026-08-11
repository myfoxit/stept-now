/**
 * Create a contact by hand.
 *
 * Until this existed, contacts could only be born from inbound widget traffic —
 * there was no create affordance anywhere in the UI, so an empty workspace had
 * no way to start an outbound conversation.
 */

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { parseApiError } from '@/lib/errors'
import { useCreateContact } from '@/features/contacts/hooks'
import type { Contact } from '@/features/contacts/api'
import { t } from '@/i18n'

const FIELDS = [
  { key: 'name', label: 'Name', placeholder: 'Ada Lovelace' },
  { key: 'email', label: 'Email', placeholder: 'ada@example.com' },
  { key: 'phone', label: 'Phone', placeholder: '+44 20 7946 0958' },
] as const

type FieldKey = (typeof FIELDS)[number]['key']

export function NewContactDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called with the new contact — lets the caller navigate or select it. */
  onCreated?: (contact: Contact) => void
}) {
  const [form, setForm] = useState<Record<FieldKey, string>>({ name: '', email: '', phone: '' })
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const create = useCreateContact()

  function reset() {
    setForm({ name: '', email: '', phone: '' })
    setFieldErrors({})
  }

  function submit() {
    setFieldErrors({})
    create.mutate(
      {
        name: form.name.trim() || undefined,
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
      },
      {
        onSuccess: (contact) => {
          toast.success(t('contacts.contact_created'))
          reset()
          onOpenChange(false)
          onCreated?.(contact)
        },
        onError: (error) => {
          const { message, fields } = parseApiError(error)
          setFieldErrors(fields)
          toast.error(message)
        },
      }
    )
  }

  // A contact with no identifier at all is not useful and the backend rejects it.
  const canSubmit = !!(form.name.trim() || form.email.trim() || form.phone.trim())

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('common.new_contact')}</DialogTitle>
          <DialogDescription>{t('contacts.add_someone_manually_give_at_least')}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          {FIELDS.map(({ key, label, placeholder }) => (
            <div key={key} className="grid gap-1.5">
              <Label htmlFor={`new-contact-${key}`}>{label}</Label>
              <Input
                id={`new-contact-${key}`}
                value={form[key]}
                placeholder={placeholder}
                aria-invalid={!!fieldErrors[key]}
                aria-describedby={fieldErrors[key] ? `new-contact-${key}-error` : undefined}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
              {fieldErrors[key] ? (
                <p id={`new-contact-${key}-error`} role="alert" className="text-xs text-destructive">
                  {fieldErrors[key]}
                </p>
              ) : null}
            </div>
          ))}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} disabled={!canSubmit || create.isPending}>
            {t('contacts.create_contact')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
