import { t } from '../../i18n'

/** Three-dot "agent is typing" indicator, styled like an incoming bubble. */
export function TypingDots() {
  return (
    <div class="sw-row sw-row-them">
      <div class="sw-avatar" aria-hidden="true">
        …
      </div>
      <div class="sw-bubble sw-bubble-them sw-typing" aria-label={t('typing.label')}>
        <span class="sw-dot" />
        <span class="sw-dot" />
        <span class="sw-dot" />
      </div>
    </div>
  )
}
