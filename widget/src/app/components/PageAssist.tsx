/**
 * The one piece of assistant UI the visitor has to see: what it may do to their
 * page, and what it is doing right now.
 *
 * Two states, both a single line in the thread — deliberately not a dialog. The
 * assistant can already look at the page and point things out once the workspace
 * turns page control on; the toggle governs only the stronger permission of
 * clicking and typing on the visitor's behalf, and it is off until they say yes.
 */

import { t, tParts } from '../../i18n'

export function PageAssist({
  pageTitle,
  actionsAllowed,
  working,
  onAllowChange,
}: {
  /** Host page title (or path) — grounds "this page" in something recognisable. */
  pageTitle: string
  actionsAllowed: boolean
  /** Non-null while a page op runs: the wire op name (`act`, `snapshot`, …). */
  working: string | null
  onAllowChange: (allowed: boolean) => void
}) {
  if (working) {
    return (
      <div class="sw-page-assist sw-page-assist-busy" role="status" aria-live="polite">
        <span class="sw-page-assist-dot" aria-hidden="true" />
        {describe(working)}
      </div>
    )
  }
  const [before, after] = tParts('page_assist.allow', 'page')
  return (
    <div class="sw-page-assist">
      <label class="sw-page-assist-toggle">
        <input
          type="checkbox"
          checked={actionsAllowed}
          onChange={(event) => onAllowChange((event.currentTarget as HTMLInputElement).checked)}
        />
        <span>
          {before}
          {pageTitle ? <strong>{pageTitle}</strong> : t('page_assist.this_page')}
          {after}
        </span>
      </label>
    </div>
  )
}

/** Plain-language status for the op in flight. */
function describe(op: string): string {
  switch (op) {
    case 'action':
      return t('page_assist.busy.action')
    case 'act':
      return t('page_assist.busy.act')
    case 'navigate':
      return t('page_assist.busy.navigate')
    case 'read':
      return t('page_assist.busy.read')
    case 'find':
    case 'snapshot':
      return t('page_assist.busy.look')
    case 'scroll':
      return t('page_assist.busy.scroll')
    case 'wait':
      return t('page_assist.busy.wait')
    default:
      return t('page_assist.busy.default')
  }
}
