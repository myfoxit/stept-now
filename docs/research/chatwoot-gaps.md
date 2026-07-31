# Chatwoot → Stept gap inventory (implementation reference)

> Source: fresh shallow clone of chatwoot/chatwoot @ `7981e2c` (2026-07-31), read for the
> competitive-parity wave. Paths are relative to the Chatwoot repo. This is the binding
> reference for the new channel adapters + SLA/macros/campaigns modules.

## 1. Channels

### 1.1 WhatsApp Cloud API — `app/models/channel/whatsapp.rb`
Table `channel_whatsapp`: `phone_number` (uniq), `provider` (`default`=360dialog | `whatsapp_cloud`), `provider_config` jsonb, `message_templates` jsonb cache.
`provider_config` keys: `api_key`, `phone_number_id`, `business_account_id`, `webhook_verify_token` (auto hex(16)), `app_secret`.

**Inbound** — `GET|POST /webhooks/whatsapp/:phone_number` → `webhooks/whatsapp_controller.rb` + `concerns/meta_token_verify_concern.rb`:
- Verify challenge: `GET` with `hub.verify_token` compared to `provider_config['webhook_verify_token']`; respond with raw `hub.challenge`.
- Signature: header `X-Hub-Signature-256` = `sha256=` + HMAC-SHA256(`app_secret`, raw body), constant-time compare.
- Payload root = `entry[0].changes[0].value`. Branch on `value.statuses` (delivery receipts: `status[:id]`, `status[:status]`, `status[:errors][0]`) vs `value.messages`.
- Message fields: `messages[0].{id, from, type, timestamp, context.id}`; content = `text.body || button.text || interactive.button_reply.title || interactive.list_reply.title`. `contacts[0].{wa_id, profile.name}` for the contact name. Skip types `reaction`/`ephemeral`; `unsupported` → placeholder.
- `source_id` (message dedupe) = `messages[0].id` (`wamid...`); contact_inbox `source_id` = phone digits (`from`).
- Media: `GET graph/v13.0/{media_id}` Bearer auth → `{url}` → download with same auth.

**Outbound** — `services/whatsapp/providers/whatsapp_cloud_service.rb`:
- `POST https://graph.facebook.com/v13.0/{phone_number_id}/messages`, `Authorization: Bearer {api_key}`, body `{messaging_product:'whatsapp', to:<msisdn>, type:'text', text:{body}}` (+ optional `context:{message_id}` for threading).
- Response `messages[0].id` → outbound message `source_id`; error `error.message` → failed + external_error.
- **24h session window**: reply allowed only within 24h of last *incoming* message (`conversations/message_window_service.rb`); outside → template message (`{type:'template', template:{name, language:{code}, components}}`) or fail with a clear error.
- Template sync: `GET graph/v14.0/{business_account_id}/message_templates` (cursor-paginated).

### 1.2 Twilio SMS — `app/models/channel/twilio_sms.rb`
Table: `account_sid`, `auth_token` (encrypted), `phone_number` (uniq) XOR `messaging_service_sid`, `medium` enum `{sms, whatsapp}`.
**Inbound**: `POST /twilio/callback` (form-encoded): `SmsSid` (=message source_id), `AccountSid`, `MessagingServiceSid`, `From`, `To`, `Body`, `ProfileName`, `NumMedia` + `MediaUrl{0..9}`. Channel lookup by `MessagingServiceSid`, else `(AccountSid, To)`. contact_inbox `source_id` = `From` (E.164). NOTE: Chatwoot does *no* signature validation here (allowlisted params only) — Stept should validate `X-Twilio-Signature` (base64 HMAC-SHA1 of full URL + alphabetically-sorted POST params, keyed by auth_token).
**Delivery status**: `POST /twilio/delivery_status`: `MessageSid`, `MessageStatus` (`sent|delivered|undelivered|failed`), `ErrorCode`, `ErrorMessage`.
**Outbound**: Twilio REST `POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json`, basic auth `(account_sid, auth_token)`, form `{To, From | MessagingServiceSid, Body, StatusCallback?}`; response `sid` → source_id.

### 1.3 Facebook Messenger — `app/models/channel/facebook_page.rb`
Table: `page_id`, `page_access_token` (enc), `instagram_id`, uniq `(page_id, account_id)`.
Inbound (facebook-messenger gem, mounted at `/bot`): `GET` verify-token challenge (same hub.* scheme), `POST` with `X-Hub-Signature-256`. `entry[].messaging[]` → `sender.id` (PSID = contact source_id), `recipient.id` (page routing), `timestamp`, `message.{mid (source_id), text, attachments, is_echo}`. Echo → outgoing on page where `page_id == sender.id`; normal → incoming where `page_id == recipient.id`.
**Outbound** `services/facebook/send_on_facebook_service.rb`: `{recipient:{id: psid}, message:{text}, messaging_type:'RESPONSE'}` → `POST graph/vX/me/messages?access_token=`; `HUMAN_AGENT` tag unlocks a 7-day window (env-gated); default window 24h. Response `message_id` → source_id.

### 1.4 Instagram — `app/models/channel/instagram.rb`
Table: `instagram_id` (uniq), `access_token` (enc), `expires_at` (auto-refresh).
**Inbound**: `GET|POST /webhooks/instagram`, requires `params['object'] == 'instagram'`, same `X-Hub-Signature-256` HMAC + verify-token challenge. `entry[].messaging[]` same shape as Messenger (sender.id = IGSID); route channel by `recipient.id == instagram_id` (echo: sender.id). Echo events processed delayed to avoid racing the send API.
Contact enrichment: `GET graph/{igsid}?fields=name,username,profile_pic,...`.
**Outbound**: `POST https://graph.instagram.com/v22.0/me/messages?access_token=`, body identical to Messenger (`{recipient:{id}, message:{text}}`); 24h window.

### 1.5 LINE — `app/models/channel/line.rb`
Table: `line_channel_id` (uniq), `line_channel_secret` (enc), `line_channel_token` (enc).
**Inbound**: `POST /webhooks/line/:line_channel_id`; signature header `x-line-signature` = `Base64(HMAC-SHA256(channel_secret, raw_body))`. Iterate `events[]` where `type == 'message'`: `source.userId` → contact source_id (profile via `GET https://api.line.me/v2/bot/profile/{userId}` Bearer channel_token → `displayName`, `pictureUrl`), `message.id` → source_id, `message.text`.
**Outbound**: `POST https://api.line.me/v2/bot/message/push`, Bearer channel_token, `{to: userId, messages:[{type:'text', text}]}` (≤5 messages/call). 200 → delivered; error body `{message, details[].{property,message}}`.

## 2. Campaigns — `app/models/campaign.rb`
Fields: `title`, `message` (required), `campaign_type` `{ongoing, one_off}`, `campaign_status` `{active, completed, processing}`, `enabled`, `inbox_id`, `sender_id`, `audience` jsonb (label refs), `trigger_rules` jsonb `{url, time_on_page}`, `trigger_only_during_business_hours`, `scheduled_at`, per-account `display_id`.
Type forced by inbox: Website → ongoing (scheduled_at nulled); SMS/WhatsApp → one_off (scheduled_at defaults now).
**Ongoing (widget)**: widget fetches `GET /api/v1/widget/campaigns?website_token=` (enabled+ongoing only), caches 1h, matches `trigger_rules.url` against the current URL client-side, arms `setTimeout(time_on_page * 1000)`, then fires an event → server builds the conversation (locks contact_inbox, *refuses if any conversation already exists* for that contact_inbox) + message authored by `campaign.sender`, `campaign_id` on the conversation.
**One-off**: cron picks `one_off + active + scheduled_at <= now` (3-day lookback) → lock, flip `processing`, resolve audience (contacts tagged with audience labels), send one message per contact, flip `completed`.

## 3. SLA policies (enterprise/)
`sla_policy`: `name`, `description`, `first_response_time_threshold` (sec), `next_response_time_threshold`, `resolution_time_threshold`, `only_during_business_hours`. `conversations.sla_policy_id` FK.
`applied_sla`: uniq `(account, sla_policy, conversation)`, `sla_status` `{active, hit, missed, active_with_misses}`, `completed_at`. Created when `sla_policy_id` set on a conversation.
`sla_event`: `event_type` `{frt, nrt, rt}`, `meta` jsonb (nrt carries a per-episode key so repeated misses are distinct), FKs applied_sla/conversation/inbox/policy. On create → notify assignee + participants + admins.
Breach eval (cron → per-account job → per-applied-SLA service `sla/evaluate_applied_sla_service.rb`):
- FRT missed when no `first_reply_created_at` and `now > created_at + frt`.
- NRT only after first reply, while `waiting_since` set: missed when `now > waiting_since + nrt` (one event per waiting episode).
- RT: skipped once resolved; missed when `now > created_at + rt`.
- Each miss: SlaEvent (dedup-guarded) + status `active_with_misses`. On resolve: `active` → `hit`, else `missed`; `completed_at` set.

## 4. Macros — `app/models/macro.rb`
`name`, `actions` jsonb (ordered `[{action_name, action_params: []}]` — same vocabulary + executor as automation rules via shared `action_service.rb`), `visibility` `{personal, global}` (agents forced personal; listing = global OR own personal), `created_by_id`. Execution iterates actions, rescues per-action errors; `assign_agent` supports literal `'self'`. Bulk run job takes `conversation_ids`.

## 5. Custom attribute definitions — `custom_attribute_definition.rb`
`attribute_display_name`, `attribute_key` (uniq per account+model), `attribute_display_type` `{text, number, currency, percent, link, date, list, checkbox}`, `attribute_values` (list options), `default_value`, `regex_pattern/regex_cue`, `attribute_model` `{conversation, contact, company}`. Values live in the record's `custom_attributes` jsonb.

## 6. Business hours — `working_hour.rb`
Per inbox: `day_of_week` (0=Sun), `open_hour/minutes`, `close_hour/minutes`, `open_all_day`, `closed_all_day`; inbox `working_hours_enabled`, `timezone`, `out_of_office_message`. Auto-seeds Mon–Fri 9–17. Widget shows OOO message; campaigns can be business-hours-gated.

## 7. Other notable (roadmap candidates)
- Assignment policies (`assignment_order` round_robin/balanced, `fair_distribution_limit/window`) + EE agent-capacity policies (per-inbox conversation_limit).
- Conversation participants/watchers; @-mentions (per-user mention rows + view).
- Contact merge action (moves conversations/messages/contact_inboxes/notes, deep-merges attrs); contact `blocked` flag (inbound dropped).
- Dashboard apps: iframe tabs in the conversation sidebar (`content: [{type:'frame', url}]`).
- Integrations registry (`integrations/hook.rb`): slack, linear, notion, shopify, dialogflow, google_translate, openai helpers.
- CSAT delivery service on resolve: WhatsApp template → Twilio content → in-conversation `input_csat` message, gated by `inbox.csat_survey_enabled` + `csat_config` (incl. label-based survey_rules).
- Full IMAP/SMTP email channel (per-inbox creds, OAuth providers, forward-to address) + ActionMailbox reply threading.
- Pre-chat forms (field definitions jsonb on widget channel), `lock_to_single_conversation`, `allow_messages_after_resolved`, greeting flags.
- Multi-portal help center with custom domains + locales; CSV contact import (data_import); saved custom filters; email templates.
