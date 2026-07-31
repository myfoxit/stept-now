import { describe, expect, it } from 'vitest'

import type { Campaign } from './api'
import { campaignTypeForChannel, describeTrigger, parseAudience } from './lib'
import { makeCampaign } from './test-utils'

describe('campaignTypeForChannel', () => {
  it('maps widget to ongoing and messaging channels to one_off', () => {
    expect(campaignTypeForChannel('widget')).toBe('ongoing')
    expect(campaignTypeForChannel('email')).toBe('one_off')
    expect(campaignTypeForChannel('sms')).toBe('one_off')
    expect(campaignTypeForChannel('whatsapp')).toBe('one_off')
  })
})

describe('describeTrigger', () => {
  it('summarizes ongoing campaigns as "On <pattern> after <n>s"', () => {
    const campaign = makeCampaign({
      trigger_rules: { url_pattern: '/pricing*', time_on_page_seconds: 30 },
    }) as Campaign
    expect(describeTrigger(campaign)).toBe('On /pricing* after 30s')
    expect(describeTrigger(makeCampaign({ trigger_rules: {} }) as Campaign)).toBe('No trigger set')
  })

  it('summarizes one_off campaigns with the scheduled time', () => {
    // Build from local components so the assertion is timezone-independent.
    const scheduled = new Date(2026, 7, 2, 9, 0).toISOString()
    const campaign = makeCampaign({
      campaign_type: 'one_off',
      trigger_rules: {},
      scheduled_at: scheduled,
    }) as Campaign
    expect(describeTrigger(campaign)).toBe('Aug 2, 09:00')
    expect(
      describeTrigger(
        makeCampaign({ campaign_type: 'one_off', trigger_rules: {}, scheduled_at: null }) as Campaign
      )
    ).toBe('Not scheduled')
  })
})

describe('parseAudience', () => {
  it('parses segment/tag audiences and falls back to all', () => {
    expect(parseAudience({ type: 'segment', segment_id: 's1' })).toEqual({
      type: 'segment',
      segment_id: 's1',
    })
    expect(parseAudience({ type: 'tag', tag_id: 't1' })).toEqual({ type: 'tag', tag_id: 't1' })
    expect(parseAudience({})).toEqual({ type: 'all' })
    expect(parseAudience({ type: 'segment' })).toEqual({ type: 'all' })
    expect(parseAudience(null)).toEqual({ type: 'all' })
  })
})
