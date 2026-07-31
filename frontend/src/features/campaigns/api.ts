/**
 * Campaigns API — proactive outbound messages.
 *
 * "ongoing" campaigns show in the widget when URL + time-on-page triggers match;
 * "one_off" campaigns are scheduled sends (email/sms/whatsapp) to an audience.
 * Supporting lookups (inboxes/segments/tags/members) mirror the canonical paths
 * used by the settings and contacts features.
 */

import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Campaign = components['schemas']['CampaignOut']
export type CampaignCreate = components['schemas']['CampaignCreate']
export type CampaignUpdate = components['schemas']['CampaignUpdate']
export type Inbox = components['schemas']['InboxOut']
export type Segment = components['schemas']['SegmentOut']
export type Tag = components['schemas']['TagOut']
export type Member = components['schemas']['MembershipOut']

export type CampaignType = 'ongoing' | 'one_off'
export type CampaignStatus = 'draft' | 'active' | 'processing' | 'completed'

export type Audience =
  | { type: 'all' }
  | { type: 'segment'; segment_id: string }
  | { type: 'tag'; tag_id: string }

export interface TriggerRules {
  url_pattern: string
  time_on_page_seconds: number
}

export const campaignsApi = {
  list: () => api.get<Campaign[]>(ws('/campaigns')),
  create: (body: CampaignCreate) => api.post<Campaign>(ws('/campaigns'), body),
  update: (id: string, body: CampaignUpdate) => api.patch<Campaign>(ws(`/campaigns/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/campaigns/${id}`)),
  activate: (id: string) => api.post<Campaign>(ws(`/campaigns/${id}/activate`)),
  pause: (id: string) => api.post<Campaign>(ws(`/campaigns/${id}/pause`)),
}

/** Supporting data for the editor (inbox picker, audience, sender). */
export const listInboxes = () => api.get<Inbox[]>(ws('/inboxes'))
export const listSegments = () => api.get<Segment[]>(ws('/segments'))
export const listTags = () => api.get<Tag[]>(ws('/tags'))
export const listMembers = () => api.get<Member[]>(ws('/members'))
