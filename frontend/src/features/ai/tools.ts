/**
 * Builtin agent tool catalog + default policies. Mirrors the backend engine's
 * DEFAULT_POLICIES (app/agents/engine.py) so the builder shows the same tools
 * and defaults an agent would use when a policy is unspecified.
 */

import type { ToolPolicy } from './api'

export interface BuiltinTool {
  key: string
  label: string
  description: string
  defaultPolicy: ToolPolicy
}

export const BUILTIN_TOOLS: BuiltinTool[] = [
  {
    key: 'search_knowledge',
    label: 'Search knowledge',
    description: 'Retrieve and cite relevant chunks from your knowledge base.',
    defaultPolicy: 'auto',
  },
  {
    key: 'handoff_to_human',
    label: 'Hand off to human',
    description: 'Escalate the conversation to a teammate.',
    defaultPolicy: 'auto',
  },
  {
    key: 'collect_contact_details',
    label: 'Collect contact details',
    description: 'Capture the contact’s name or email during the chat.',
    defaultPolicy: 'auto',
  },
  {
    key: 'tag_conversation',
    label: 'Tag conversation',
    description: 'Apply an existing tag to organise the conversation.',
    defaultPolicy: 'auto',
  },
  {
    key: 'note_to_team',
    label: 'Note to team',
    description: 'Post a private internal note for teammates.',
    defaultPolicy: 'auto',
  },
  {
    key: 'close_conversation',
    label: 'Close conversation',
    description: 'Resolve the conversation once the issue is handled.',
    defaultPolicy: 'require_approval',
  },
]

export const TOOL_POLICIES: { value: ToolPolicy; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', hint: 'Runs without asking' },
  { value: 'require_approval', label: 'Approval', hint: 'Waits for a human' },
  { value: 'disabled', label: 'Off', hint: 'Never used' },
]

export const DEFAULT_ACTION_POLICY: ToolPolicy = 'require_approval'

/** Resolve the effective policy for a tool key from the agent's tool list. */
export function policyForTool(
  tools: { key: string; policy: ToolPolicy }[],
  key: string,
  fallback: ToolPolicy
): ToolPolicy {
  return tools.find((t) => t.key === key)?.policy ?? fallback
}
