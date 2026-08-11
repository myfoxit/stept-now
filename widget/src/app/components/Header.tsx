import { useEffect, useState } from 'preact/hooks'

import { t } from '../../i18n'

/**
 * Should the header render its own ✕?
 *
 * On desktop the loader keeps the launcher visible under the open panel and
 * turns it into the close button — a second ✕ in the header is duplicate
 * chrome. On phones (and narrow desktop windows) the loader's max-width:480px
 * rule makes the iframe fullscreen and fades the launcher out, so the header
 * must supply the only close affordance.
 *
 * The iframe cannot read the host viewport, but the two layouts differ
 * measurably from inside: the desktop panel is exactly 400 CSS px wide
 * (loader.ts CSS), while the fullscreen iframe tracks the host window. A
 * narrow physical screen is always the fullscreen case.
 */
export function needsHeaderClose(frameWidth: number, screenWidth: number): boolean {
  if (!screenWidth || screenWidth <= 480) return true
  return frameWidth !== 400
}

function useHeaderClose(): boolean {
  const measure = (): boolean => needsHeaderClose(window.innerWidth, window.screen?.width ?? 0)
  const [visible, setVisible] = useState(measure)
  useEffect(() => {
    const onResize = (): void => setVisible(measure())
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return visible
}

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
  const closeVisible = useHeaderClose()
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
      {closeVisible && (
        <button type="button" class="sw-icon-btn" aria-label={t('header.close')} onClick={onClose}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      )}
    </header>
  )
}
