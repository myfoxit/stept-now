import { t } from '../../i18n'

/**
 * Shown when boot returns `require_identity` and the host page did not supply a
 * verified identity. The widget cannot itself mint the identity HMAC (that
 * secret lives server-side), so this explains the requirement and offers a
 * retry — which succeeds once the host injects `SteptSettings.identity` (e.g.
 * after the visitor logs in).
 */
export function IdentityGate({
  workspaceName,
  onRetry,
}: {
  workspaceName: string
  onRetry: () => void
}) {
  return (
    <div class="sw-gate">
      <div class="sw-gate-icon" aria-hidden="true">
        🔒
      </div>
      <h2 class="sw-gate-title">
        {t('identity.title', { workspace: workspaceName || t('identity.workspace_fallback') })}
      </h2>
      <p class="sw-gate-body">{t('identity.body')}</p>
      <button type="button" class="sw-btn sw-btn-primary" onClick={onRetry}>
        {t('identity.retry')}
      </button>
    </div>
  )
}
