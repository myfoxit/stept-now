import { describe, expect, it } from 'vitest'

import { WEBHOOK_EVENTS } from './constants'

/**
 * Mirror of backend/app/core/events.py `EventNames`, in declaration order.
 * If this test fails you almost certainly added/removed an event on the
 * backend — update BOTH `WEBHOOK_EVENTS` in constants.ts and this list.
 */
const BACKEND_EVENT_NAMES = [
  'workspace.created',
  'member.joined',
  'contact.created',
  'conversation.created',
  'conversation.updated',
  'conversation.assigned',
  'conversation.status_changed',
  'message.created',
  'csat.submitted',
  'message.feedback',
  'sla.breached',
  'campaign.sent',
  'document.indexed',
  'integration.connected',
  'integration.disconnected',
  'integration.reauth_required',
  'agent_run.started',
  'agent_run.completed',
  'approval.requested',
  'approval.decided',
  'tour.event',
  'checklist.event',
  'survey.submitted',
]

describe('WEBHOOK_EVENTS', () => {
  it('offers "*" first, then every backend event in catalog order', () => {
    expect(WEBHOOK_EVENTS).toEqual(['*', ...BACKEND_EVENT_NAMES])
  })

  it('contains no duplicates', () => {
    expect(new Set(WEBHOOK_EVENTS).size).toBe(WEBHOOK_EVENTS.length)
  })
})
