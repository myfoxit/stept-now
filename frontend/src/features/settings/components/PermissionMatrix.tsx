import { Checkbox } from '@/components/ui/checkbox'

/** Group "conversations:read" → { conversations: ["conversations:read", …] }. */
export function groupPermissions(permissions: string[]): Record<string, string[]> {
  const groups: Record<string, string[]> = {}
  for (const perm of permissions) {
    const [resource] = perm.split(':')
    ;(groups[resource!] ??= []).push(perm)
  }
  return groups
}

function actionLabel(perm: string): string {
  const [, action = ''] = perm.split(':')
  return action.replace(/_/g, ' ')
}

export function PermissionMatrix({
  all,
  selected,
  onChange,
  disabled = false,
}: {
  all: string[]
  selected: string[]
  onChange: (permissions: string[]) => void
  disabled?: boolean
}) {
  const selectedSet = new Set(selected)
  const groups = groupPermissions(all)

  function toggle(perm: string) {
    const next = new Set(selectedSet)
    if (next.has(perm)) next.delete(perm)
    else next.add(perm)
    onChange([...next])
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {Object.entries(groups).map(([resource, perms]) => (
        <div key={resource} className="grid gap-2 rounded-md border p-3">
          <div className="text-xs font-semibold capitalize text-muted-foreground">{resource}</div>
          {perms.map((perm) => (
            <label key={perm} className="flex items-center gap-2 text-sm capitalize" htmlFor={perm}>
              <Checkbox
                id={perm}
                checked={selectedSet.has(perm)}
                disabled={disabled}
                onCheckedChange={() => toggle(perm)}
              />
              {actionLabel(perm)}
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}
