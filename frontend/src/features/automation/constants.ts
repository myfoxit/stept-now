/**
 * Option catalogs for the automation rule + webhook builders. These mirror the
 * backend contracts (app/schemas/automations.py, app/automation/{conditions,actions}.py,
 * app/core/events.py) exactly — keep them in sync when the backend changes.
 */

export interface Option {
  value: string
  label: string
}

/** Events an automation rule may target (AutomationEvent). */
export const AUTOMATION_EVENTS: Option[] = [
  { value: 'conversation.created', label: 'Conversation created' },
  { value: 'message.created', label: 'Message received' },
  { value: 'conversation.status_changed', label: 'Conversation status changed' },
  { value: 'csat.submitted', label: 'CSAT submitted' },
  { value: 'contact.created', label: 'Contact created' },
]

/** Condition operators (ConditionOp). */
export const CONDITION_OPS: Option[] = [
  { value: 'eq', label: 'is' },
  { value: 'neq', label: 'is not' },
  { value: 'contains', label: 'contains' },
  { value: 'in', label: 'is any of' },
  { value: 'exists', label: 'exists' },
]

/** Ops whose value input is hidden (no operand needed). */
export const VALUELESS_OPS = new Set(['exists'])
/** Ops whose value is a comma-separated list. */
export const LIST_OPS = new Set(['in'])

/**
 * Condition fields the engine can resolve (app/automation/conditions.py). Freeform
 * `contact.attributes.<key>` is also accepted; the builder allows a custom field.
 */
export const CONDITION_FIELDS: Option[] = [
  { value: 'status', label: 'Status' },
  { value: 'priority', label: 'Priority' },
  { value: 'channel_type', label: 'Channel' },
  { value: 'inbox_id', label: 'Inbox' },
  { value: 'subject', label: 'Subject' },
  { value: 'content', label: 'Message content' },
  { value: 'contact.email', label: 'Contact email' },
  { value: 'tag', label: 'Tag' },
]

export const STATUS_OPTIONS: Option[] = [
  { value: 'open', label: 'Open' },
  { value: 'pending', label: 'Pending' },
  { value: 'snoozed', label: 'Snoozed' },
  { value: 'resolved', label: 'Resolved' },
]

export const PRIORITY_OPTIONS: Option[] = [
  { value: 'none', label: 'None' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'urgent', label: 'Urgent' },
]

export const CHANNEL_OPTIONS: Option[] = [
  { value: 'widget', label: 'Widget' },
  { value: 'email', label: 'Email' },
  { value: 'slack', label: 'Slack' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'api', label: 'API' },
]

/** A single configurable parameter on an action. */
export interface ParamField {
  key: string
  label: string
  kind: 'text' | 'textarea' | 'select'
  options?: Option[]
  placeholder?: string
}

/** Action types + their parameter shape (app/automation/actions.py). */
export const ACTION_SPECS: Record<string, { label: string; params: ParamField[] }> = {
  assign_user: {
    label: 'Assign to member',
    params: [{ key: 'user_id', label: 'Member ID', kind: 'text', placeholder: 'user id' }],
  },
  assign_team: {
    label: 'Assign to team',
    params: [{ key: 'team_id', label: 'Team ID', kind: 'text', placeholder: 'team id' }],
  },
  set_priority: {
    label: 'Set priority',
    params: [{ key: 'priority', label: 'Priority', kind: 'select', options: PRIORITY_OPTIONS }],
  },
  set_status: {
    label: 'Set status',
    params: [{ key: 'status', label: 'Status', kind: 'select', options: STATUS_OPTIONS }],
  },
  add_tag: {
    label: 'Add tag',
    params: [{ key: 'tag', label: 'Tag name', kind: 'text', placeholder: 'e.g. vip' }],
  },
  send_reply: {
    label: 'Send reply',
    params: [{ key: 'content', label: 'Message', kind: 'textarea', placeholder: 'Reply text…' }],
  },
  send_note: {
    label: 'Add private note',
    params: [{ key: 'content', label: 'Note', kind: 'textarea', placeholder: 'Internal note…' }],
  },
  notify_member: {
    label: 'Notify member',
    params: [
      { key: 'user_id', label: 'Member ID', kind: 'text', placeholder: 'user id' },
      { key: 'title', label: 'Title', kind: 'text', placeholder: 'Notification title' },
      { key: 'body', label: 'Body', kind: 'text', placeholder: 'Notification body' },
    ],
  },
  send_webhook: {
    label: 'Send webhook',
    params: [{ key: 'webhook_id', label: 'Webhook ID', kind: 'text', placeholder: 'webhook id' }],
  },
}

export const ACTION_TYPES: Option[] = Object.entries(ACTION_SPECS).map(([value, spec]) => ({
  value,
  label: spec.label,
}))

/**
 * Action types a macro may contain (app/schemas/macros.py MacroAction). Same row
 * builder as automation rules, but a slightly different catalog: `remove_tag`
 * exists, `assign_user` accepts the literal "self", and notify/webhook don't apply.
 */
export const MACRO_ACTION_SPECS: Record<string, { label: string; params: ParamField[] }> = {
  assign_user: {
    label: 'Assign to member',
    params: [{ key: 'user_id', label: 'Member ID', kind: 'text', placeholder: 'user id or "self"' }],
  },
  assign_team: {
    label: 'Assign to team',
    params: [{ key: 'team_id', label: 'Team ID', kind: 'text', placeholder: 'team id' }],
  },
  set_priority: {
    label: 'Set priority',
    params: [{ key: 'priority', label: 'Priority', kind: 'select', options: PRIORITY_OPTIONS }],
  },
  set_status: {
    label: 'Set status',
    params: [{ key: 'status', label: 'Status', kind: 'select', options: STATUS_OPTIONS }],
  },
  add_tag: {
    label: 'Add tag',
    params: [{ key: 'tag', label: 'Tag name', kind: 'text', placeholder: 'e.g. vip' }],
  },
  remove_tag: {
    label: 'Remove tag',
    params: [{ key: 'tag', label: 'Tag name', kind: 'text', placeholder: 'e.g. vip' }],
  },
  send_reply: {
    label: 'Send reply',
    params: [{ key: 'content', label: 'Message', kind: 'textarea', placeholder: 'Reply text…' }],
  },
  send_note: {
    label: 'Add private note',
    params: [{ key: 'content', label: 'Note', kind: 'textarea', placeholder: 'Internal note…' }],
  },
}

export const MACRO_ACTION_TYPES: Option[] = Object.entries(MACRO_ACTION_SPECS).map(
  ([value, spec]) => ({ value, label: spec.label })
)

/** Domain events a webhook can subscribe to (app/core/events.py EventNames) + "*". */
export const WEBHOOK_EVENTS: string[] = [
  '*',
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
