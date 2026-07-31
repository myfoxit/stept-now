import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import {
  emailInbox,
  makeCampaign,
  member,
  resetAuth,
  seedAuth,
  segment,
  tag,
  widgetInbox,
} from '../test-utils'
import { CampaignEditorDialog } from './CampaignEditorDialog'

const baseRoutes = {
  'GET /api/v1/w/w1/inboxes': () => ({ body: [widgetInbox, emailInbox] }),
  'GET /api/v1/w/w1/segments': () => ({ body: [segment] }),
  'GET /api/v1/w/w1/tags': () => ({ body: [tag] }),
  'GET /api/v1/w/w1/members': () => ({ body: [member] }),
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

/** Wait for the inbox options to load, then pick one. */
async function pickInbox(value: 'i1' | 'i2') {
  const select = await screen.findByLabelText('Inbox')
  await screen.findByRole('option', { name: value === 'i1' ? /Website widget/ : /Support email/ })
  await userEvent.selectOptions(select, value)
  return select
}

describe('CampaignEditorDialog', () => {
  it('switches between trigger and schedule fields based on the inbox channel', async () => {
    mockFetch(baseRoutes)
    renderApp(<CampaignEditorDialog open onOpenChange={() => {}} campaign={null} />)

    await screen.findByLabelText('Inbox')
    await screen.findByRole('option', { name: /Website widget/ })

    // No conditional fields until an inbox is picked.
    expect(screen.queryByLabelText(/Show on pages matching/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Send at')).not.toBeInTheDocument()

    // Widget inbox → ongoing: trigger fields, no schedule.
    const inboxSelect = await pickInbox('i1')
    expect(screen.getByText(/In-app — shows in the widget/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Show on pages matching/)).toBeInTheDocument()
    expect(screen.getByLabelText(/After time on page/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Send at')).not.toBeInTheDocument()

    // Email inbox → one_off: schedule + audience + sender, no trigger fields.
    await userEvent.selectOptions(inboxSelect, 'i2')
    expect(screen.getByText(/Scheduled — sent via email/)).toBeInTheDocument()
    expect(screen.getByLabelText('Send at')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Everyone' })).toBeInTheDocument()
    expect(screen.getByLabelText('Send as')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Show on pages matching/)).not.toBeInTheDocument()
  })

  it('blocks submission when the message is empty', async () => {
    const fetchFn = mockFetch(baseRoutes)
    renderApp(<CampaignEditorDialog open onOpenChange={() => {}} campaign={null} />)

    await pickInbox('i1')
    await userEvent.type(screen.getByLabelText('Title'), 'Pricing nudge')
    await userEvent.type(screen.getByLabelText(/Show on pages matching/), '/pricing*')
    await userEvent.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(await screen.findByText('Message is required')).toBeInTheDocument()
    expect(fetchFn.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('creates an ongoing campaign with serialized trigger rules', async () => {
    let postBody: Record<string, unknown> | undefined
    const onOpenChange = vi.fn()
    mockFetch({
      ...baseRoutes,
      'POST /api/v1/w/w1/campaigns': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: makeCampaign() }
      },
    })
    renderApp(<CampaignEditorDialog open onOpenChange={onOpenChange} campaign={null} />)

    await pickInbox('i1')
    await userEvent.type(screen.getByLabelText('Title'), 'Pricing nudge')
    await userEvent.type(screen.getByLabelText('Message'), 'Hi {{contact.name}}!')
    await userEvent.type(
      screen.getByLabelText(/Show on pages matching/),
      'https://app.example.com/pricing*'
    )
    const seconds = screen.getByLabelText(/After time on page/)
    await userEvent.clear(seconds)
    await userEvent.type(seconds, '45')
    await userEvent.click(screen.getByRole('button', { name: 'Create campaign' }))

    await waitFor(() => expect(postBody).toBeDefined())
    expect(postBody).toMatchObject({
      title: 'Pricing nudge',
      inbox_id: 'i1',
      campaign_type: 'ongoing',
      enabled: true,
      trigger_rules: { url_pattern: 'https://app.example.com/pricing*', time_on_page_seconds: 45 },
    })
    expect(postBody).not.toHaveProperty('scheduled_at')
    expect(postBody).not.toHaveProperty('audience')
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('serializes a segment audience for one_off campaigns', async () => {
    let postBody: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes,
      'POST /api/v1/w/w1/campaigns': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: makeCampaign({ campaign_type: 'one_off' }) }
      },
    })
    renderApp(<CampaignEditorDialog open onOpenChange={() => {}} campaign={null} />)

    await pickInbox('i2')
    await userEvent.type(screen.getByLabelText('Title'), 'Summer promo')
    await userEvent.type(screen.getByLabelText('Message'), 'Big news!')
    fireEvent.change(screen.getByLabelText('Send at'), { target: { value: '2026-08-02T09:00' } })

    await userEvent.click(screen.getByRole('radio', { name: 'Segment' }))
    const segmentSelect = await screen.findByLabelText('Choose segment')
    await screen.findByRole('option', { name: 'VIP customers' })
    await userEvent.selectOptions(segmentSelect, 's1')
    await screen.findByRole('option', { name: 'Bob' })
    await userEvent.selectOptions(screen.getByLabelText('Send as'), 'u2')
    await userEvent.click(screen.getByRole('button', { name: 'Create campaign' }))

    await waitFor(() => expect(postBody).toBeDefined())
    expect(postBody).toMatchObject({
      title: 'Summer promo',
      inbox_id: 'i2',
      campaign_type: 'one_off',
      audience: { type: 'segment', segment_id: 's1' },
      sender_user_id: 'u2',
    })
    expect(postBody!.scheduled_at).toBe(new Date('2026-08-02T09:00').toISOString())
    expect(postBody).not.toHaveProperty('trigger_rules')
  })

  it('requires a send time and a segment before creating a one_off campaign', async () => {
    const fetchFn = mockFetch(baseRoutes)
    renderApp(<CampaignEditorDialog open onOpenChange={() => {}} campaign={null} />)

    await pickInbox('i2')
    await userEvent.type(screen.getByLabelText('Title'), 'Summer promo')
    await userEvent.type(screen.getByLabelText('Message'), 'Big news!')
    await userEvent.click(screen.getByRole('radio', { name: 'Segment' }))
    await userEvent.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(await screen.findByText('Pick a send time')).toBeInTheDocument()
    expect(screen.getByText('Pick a segment')).toBeInTheDocument()
    expect(fetchFn.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('edits an existing campaign via PATCH without inbox or type fields', async () => {
    let patchBody: Record<string, unknown> | undefined
    const existing = makeCampaign({ id: 'c9', title: 'Pricing nudge' })
    mockFetch({
      ...baseRoutes,
      'PATCH /api/v1/w/w1/campaigns/c9': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: makeCampaign({ id: 'c9', title: 'Renamed' }) }
      },
    })
    renderApp(
      <CampaignEditorDialog open onOpenChange={() => {}} campaign={existing as never} />
    )

    const inboxSelect = await screen.findByLabelText('Inbox')
    expect(inboxSelect).toBeDisabled()

    const title = screen.getByLabelText('Title')
    await userEvent.clear(title)
    await userEvent.type(title, 'Renamed')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(patchBody).toBeDefined())
    expect(patchBody).toMatchObject({
      title: 'Renamed',
      trigger_rules: { url_pattern: '/pricing*', time_on_page_seconds: 30 },
    })
    expect(patchBody).not.toHaveProperty('inbox_id')
    expect(patchBody).not.toHaveProperty('campaign_type')
  })
})
