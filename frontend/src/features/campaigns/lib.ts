/**
 * Pure helpers for the campaigns feature: type inference from inbox channel,
 * audience/trigger (de)serialization and human-readable summaries.
 */

import { format, parseISO } from 'date-fns'

import type { Audience, Campaign, CampaignType, TriggerRules } from './api'

/** Widget inboxes host in-app ("ongoing") campaigns; every other channel is a scheduled send. */
export function campaignTypeForChannel(channelType: string): CampaignType {
  return channelType === 'widget' ? 'ongoing' : 'one_off'
}

export function parseTriggerRules(raw: Record<string, unknown> | null | undefined): TriggerRules {
  const rules = raw ?? {}
  return {
    url_pattern: typeof rules.url_pattern === 'string' ? rules.url_pattern : '',
    time_on_page_seconds:
      typeof rules.time_on_page_seconds === 'number' ? rules.time_on_page_seconds : 30,
  }
}

export function parseAudience(raw: Record<string, unknown> | null | undefined): Audience {
  const audience = raw ?? {}
  if (audience.type === 'segment' && typeof audience.segment_id === 'string') {
    return { type: 'segment', segment_id: audience.segment_id }
  }
  if (audience.type === 'tag' && typeof audience.tag_id === 'string') {
    return { type: 'tag', tag_id: audience.tag_id }
  }
  return { type: 'all' }
}

/** "Aug 2, 09:00" — schedule summary for list rows. */
export function formatSchedule(iso: string): string {
  return format(parseISO(iso), 'MMM d, HH:mm')
}

/** "On /pricing* after 30s" for ongoing, "Aug 2, 09:00" for one_off. */
export function describeTrigger(campaign: Campaign): string {
  if (campaign.campaign_type === 'ongoing') {
    const { url_pattern, time_on_page_seconds } = parseTriggerRules(campaign.trigger_rules)
    if (!url_pattern) return 'No trigger set'
    return `On ${url_pattern} after ${time_on_page_seconds}s`
  }
  return campaign.scheduled_at ? formatSchedule(campaign.scheduled_at) : 'Not scheduled'
}
