import { zodResolver } from '@hookform/resolvers/zod'
import { format, parseISO } from 'date-fns'
import { CalendarClock, Megaphone } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { Badge } from '@/components/ui/badge'
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
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'

import type { Audience, Campaign, CampaignCreate, CampaignType, CampaignUpdate } from '../api'
import {
  useCampaignInboxes,
  useCampaignMembers,
  useCampaignSegments,
  useCampaignTags,
  useCreateCampaign,
  useUpdateCampaign,
} from '../hooks'
import { campaignTypeForChannel, parseAudience, parseTriggerRules } from '../lib'

const AUDIENCE_TYPES = ['all', 'segment', 'tag'] as const

function makeSchema(resolveType: () => CampaignType | null) {
  return z
    .object({
      inbox_id: z.string().min(1, 'Pick an inbox'),
      title: z.string().trim().min(1, 'Title is required'),
      message: z.string().trim().min(1, 'Message is required'),
      url_pattern: z.string().trim(),
      time_on_page_seconds: z.coerce
        .number()
        .int('Whole seconds only')
        .min(0, 'Must be 0 or more'),
      scheduled_at: z.string(),
      audience_type: z.enum(AUDIENCE_TYPES),
      segment_id: z.string(),
      tag_id: z.string(),
      sender_user_id: z.string(),
    })
    .superRefine((values, ctx) => {
      const type = resolveType()
      if (type === 'ongoing' && !values.url_pattern) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['url_pattern'],
          message: 'URL pattern is required',
        })
      }
      if (type === 'one_off') {
        if (!values.scheduled_at) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['scheduled_at'],
            message: 'Pick a send time',
          })
        }
        if (values.audience_type === 'segment' && !values.segment_id) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['segment_id'],
            message: 'Pick a segment',
          })
        }
        if (values.audience_type === 'tag' && !values.tag_id) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['tag_id'], message: 'Pick a tag' })
        }
      }
    })
}

type FormValues = z.infer<ReturnType<typeof makeSchema>>

function defaultsFor(campaign: Campaign | null): FormValues {
  if (!campaign) {
    return {
      inbox_id: '',
      title: '',
      message: '',
      url_pattern: '',
      time_on_page_seconds: 30,
      scheduled_at: '',
      audience_type: 'all',
      segment_id: '',
      tag_id: '',
      sender_user_id: '',
    }
  }
  const trigger = parseTriggerRules(campaign.trigger_rules)
  const audience = parseAudience(campaign.audience)
  return {
    inbox_id: campaign.inbox_id,
    title: campaign.title,
    message: campaign.message,
    url_pattern: trigger.url_pattern,
    time_on_page_seconds: trigger.time_on_page_seconds,
    scheduled_at: campaign.scheduled_at
      ? format(parseISO(campaign.scheduled_at), "yyyy-MM-dd'T'HH:mm")
      : '',
    audience_type: audience.type,
    segment_id: audience.type === 'segment' ? audience.segment_id : '',
    tag_id: audience.type === 'tag' ? audience.tag_id : '',
    sender_user_id: campaign.sender_user_id ?? '',
  }
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return <p className="text-xs text-destructive">{message}</p>
}

export function CampaignEditorDialog({
  open,
  onOpenChange,
  campaign,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  campaign?: Campaign | null
}) {
  const editing = campaign ?? null

  // The schema resolves the campaign type at validation time via a ref, so a
  // single stable schema handles the type switching driven by the inbox picker.
  const typeRef = useRef<CampaignType | null>(null)
  const schema = useMemo(() => makeSchema(() => typeRef.current), [])

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: defaultsFor(editing),
  })

  const inboxes = useCampaignInboxes(open)
  const inboxId = form.watch('inbox_id')
  const selectedInbox = (inboxes.data ?? []).find((inbox) => inbox.id === inboxId) ?? null
  const type: CampaignType | null = editing
    ? (editing.campaign_type as CampaignType)
    : selectedInbox
      ? campaignTypeForChannel(selectedInbox.channel_type)
      : null
  typeRef.current = type

  const audienceType = form.watch('audience_type')
  const oneOff = open && type === 'one_off'
  const segments = useCampaignSegments(oneOff)
  const tags = useCampaignTags(oneOff)
  const members = useCampaignMembers(oneOff)

  const create = useCreateCampaign()
  const update = useUpdateCampaign()
  const saving = create.isPending || update.isPending

  useEffect(() => {
    if (open) form.reset(defaultsFor(editing))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editing])

  async function onSubmit(values: FormValues) {
    if (!type) return
    const base = { title: values.title.trim(), message: values.message.trim() }
    const details: Partial<CampaignCreate> =
      type === 'ongoing'
        ? {
            trigger_rules: {
              url_pattern: values.url_pattern.trim(),
              time_on_page_seconds: values.time_on_page_seconds,
            },
          }
        : {
            scheduled_at: new Date(values.scheduled_at).toISOString(),
            audience: serializeAudience(values),
            sender_user_id: values.sender_user_id || null,
          }
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, body: { ...base, ...details } as CampaignUpdate })
      } else {
        await create.mutateAsync({
          ...base,
          ...details,
          inbox_id: values.inbox_id,
          campaign_type: type,
          enabled: true,
        })
      }
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  const errors = form.formState.errors

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? 'Edit campaign' : 'New campaign'}</DialogTitle>
          <DialogDescription>
            {editing
              ? 'Update the message and delivery settings.'
              : 'Pick an inbox first — it decides how the campaign is delivered.'}
          </DialogDescription>
        </DialogHeader>

        <form
          className="grid gap-5 py-2"
          onSubmit={form.handleSubmit(onSubmit)}
          noValidate
          aria-label={editing ? 'Edit campaign' : 'New campaign'}
        >
          <div className="grid gap-2">
            <Label htmlFor="campaign-inbox">Inbox</Label>
            <NativeSelect
              id="campaign-inbox"
              className="w-full"
              disabled={editing !== null}
              {...form.register('inbox_id')}
            >
              <NativeSelectOption value="">Select an inbox…</NativeSelectOption>
              {(inboxes.data ?? []).map((inbox) => (
                <NativeSelectOption key={inbox.id} value={inbox.id}>
                  {inbox.name} · {inbox.channel_type}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <FieldError message={errors.inbox_id?.message} />
            {type === 'ongoing' ? (
              <Badge variant="secondary" className="w-fit">
                <Megaphone className="size-3" /> In-app — shows in the widget
              </Badge>
            ) : type === 'one_off' ? (
              <Badge variant="secondary" className="w-fit">
                <CalendarClock className="size-3" /> Scheduled — sent via{' '}
                {selectedInbox?.channel_type ?? 'this channel'}
              </Badge>
            ) : null}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="campaign-title">Title</Label>
            <Input
              id="campaign-title"
              placeholder="e.g. Announce the new pricing"
              {...form.register('title')}
            />
            <FieldError message={errors.title?.message} />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="campaign-message">Message</Label>
            <Textarea
              id="campaign-message"
              rows={4}
              placeholder="Hi {{contact.name}}, have you seen…"
              {...form.register('message')}
            />
            <p className="text-xs text-muted-foreground">
              {'{{contact.name}}'} inserts the contact&rsquo;s name.
            </p>
            <FieldError message={errors.message?.message} />
          </div>

          {type === 'ongoing' ? (
            <>
              <Separator />
              <div className="grid gap-2">
                <Label htmlFor="campaign-url">Show on pages matching</Label>
                <Input
                  id="campaign-url"
                  placeholder="https://app.example.com/pricing*"
                  {...form.register('url_pattern')}
                />
                <FieldError message={errors.url_pattern?.message} />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="campaign-seconds">After time on page (seconds)</Label>
                <Input
                  id="campaign-seconds"
                  type="number"
                  min={0}
                  className="w-32"
                  {...form.register('time_on_page_seconds')}
                />
                <FieldError message={errors.time_on_page_seconds?.message} />
              </div>
            </>
          ) : null}

          {type === 'one_off' ? (
            <>
              <Separator />
              <div className="grid gap-2">
                <Label htmlFor="campaign-scheduled">Send at</Label>
                <Input
                  id="campaign-scheduled"
                  type="datetime-local"
                  className="w-fit"
                  {...form.register('scheduled_at')}
                />
                <FieldError message={errors.scheduled_at?.message} />
              </div>

              <div className="grid gap-2">
                <Label>Audience</Label>
                <RadioGroup
                  className="flex flex-wrap gap-4"
                  value={audienceType}
                  onValueChange={(value) =>
                    form.setValue('audience_type', value as FormValues['audience_type'])
                  }
                >
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="all" id="audience-all" />
                    <Label htmlFor="audience-all" className="font-normal">
                      Everyone
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="segment" id="audience-segment" />
                    <Label htmlFor="audience-segment" className="font-normal">
                      Segment
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="tag" id="audience-tag" />
                    <Label htmlFor="audience-tag" className="font-normal">
                      Tag
                    </Label>
                  </div>
                </RadioGroup>
                {audienceType === 'segment' ? (
                  <>
                    <NativeSelect
                      aria-label="Choose segment"
                      className="w-full"
                      {...form.register('segment_id')}
                    >
                      <NativeSelectOption value="">Select a segment…</NativeSelectOption>
                      {(segments.data ?? []).map((segment) => (
                        <NativeSelectOption key={segment.id} value={segment.id}>
                          {segment.name}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                    <FieldError message={errors.segment_id?.message} />
                  </>
                ) : null}
                {audienceType === 'tag' ? (
                  <>
                    <NativeSelect
                      aria-label="Choose tag"
                      className="w-full"
                      {...form.register('tag_id')}
                    >
                      <NativeSelectOption value="">Select a tag…</NativeSelectOption>
                      {(tags.data ?? []).map((tag) => (
                        <NativeSelectOption key={tag.id} value={tag.id}>
                          {tag.name}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                    <FieldError message={errors.tag_id?.message} />
                  </>
                ) : null}
              </div>

              <div className="grid gap-2">
                <Label htmlFor="campaign-sender">Send as</Label>
                <NativeSelect
                  id="campaign-sender"
                  className="w-full"
                  {...form.register('sender_user_id')}
                >
                  <NativeSelectOption value="">Workspace default</NativeSelectOption>
                  {(members.data ?? []).map((member) => (
                    <NativeSelectOption key={member.id} value={member.user.id}>
                      {member.user.name}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
            </>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? 'Saving…' : editing ? 'Save changes' : 'Create campaign'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function serializeAudience(values: FormValues): Audience {
  if (values.audience_type === 'segment') return { type: 'segment', segment_id: values.segment_id }
  if (values.audience_type === 'tag') return { type: 'tag', tag_id: values.tag_id }
  return { type: 'all' }
}
