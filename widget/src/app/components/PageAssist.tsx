/**
 * The one piece of assistant UI the visitor has to see: what it may do to their
 * page, and what it is doing right now.
 *
 * Two states, both a single line in the thread — deliberately not a dialog. The
 * assistant can already look at the page and point things out once the workspace
 * turns page control on; the toggle governs only the stronger permission of
 * clicking and typing on the visitor's behalf, and it is off until they say yes.
 */

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
  return (
    <div class="sw-page-assist">
      <label class="sw-page-assist-toggle">
        <input
          type="checkbox"
          checked={actionsAllowed}
          onChange={(event) => onAllowChange((event.currentTarget as HTMLInputElement).checked)}
        />
        <span>
          Let the assistant do things on {pageTitle ? <strong>{pageTitle}</strong> : 'this page'} for
          me
        </span>
      </label>
    </div>
  )
}

/** Plain-language status for the op in flight. */
function describe(op: string): string {
  switch (op) {
    case 'act':
      return 'Doing that on the page…'
    case 'navigate':
      return 'Taking you to the right page…'
    case 'read':
      return 'Reading this page…'
    case 'find':
    case 'snapshot':
      return 'Looking at this page…'
    case 'scroll':
      return 'Scrolling to it…'
    case 'wait':
      return 'Waiting for the page…'
    default:
      return 'Working on the page…'
  }
}
