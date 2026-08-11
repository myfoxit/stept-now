import { t } from '../../i18n'

export function Header({
  title,
  subtitle,
  logoUrl,
  onBack,
  onClose,
}: {
  title: string
  subtitle?: string
  logoUrl?: string | null
  onBack?: () => void
  onClose: () => void
}) {
  return (
    <header class="sw-header">
      {onBack ? (
        <button type="button" class="sw-icon-btn" aria-label={t('header.back')} onClick={onBack}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
      ) : logoUrl ? (
        <img class="sw-logo" src={logoUrl} alt="" />
      ) : null}
      <div class="sw-header-text">
        <div class="sw-header-title">{title}</div>
        {subtitle && <div class="sw-header-sub">{subtitle}</div>}
      </div>
      <button type="button" class="sw-icon-btn" aria-label={t('header.close')} onClick={onClose}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </header>
  )
}
