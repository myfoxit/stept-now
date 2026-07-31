import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ToolPolicyMatrix, type PolicyRow } from './ToolPolicyMatrix'
import type { ToolPolicy } from '../api'

const ROWS: PolicyRow[] = [
  { key: 'search_knowledge', label: 'Search knowledge' },
  { key: 'close_conversation', label: 'Close conversation' },
]

function Harness({ onChange }: { onChange?: (k: string, p: ToolPolicy) => void }) {
  const [value, setValue] = useState<Record<string, ToolPolicy>>({
    search_knowledge: 'auto',
    close_conversation: 'require_approval',
  })
  return (
    <ToolPolicyMatrix
      rows={ROWS}
      value={value}
      onChange={(k, p) => {
        setValue((prev) => ({ ...prev, [k]: p }))
        onChange?.(k, p)
      }}
    />
  )
}

describe('ToolPolicyMatrix', () => {
  it('reflects the initial policy per tool', () => {
    render(<Harness />)
    expect(screen.getByRole('button', { name: 'Search knowledge: Auto' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByRole('button', { name: 'Close conversation: Approval' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('updates the policy for a tool when a segment is clicked', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await userEvent.click(screen.getByRole('button', { name: 'Search knowledge: Off' }))

    expect(onChange).toHaveBeenCalledWith('search_knowledge', 'disabled')
    expect(screen.getByRole('button', { name: 'Search knowledge: Off' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByRole('button', { name: 'Search knowledge: Auto' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
    // The other tool's policy is untouched.
    expect(screen.getByRole('button', { name: 'Close conversation: Approval' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })
})
