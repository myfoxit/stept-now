/**
 * One-shot handoff of a report drill-down into the inbox.
 *
 * A report row's filter document is too large for a URL and is meaningless to
 * bookmark, so it is parked here and consumed once by the conversation list.
 */

import { create } from 'zustand'

import type { FilterQuery } from '@/features/inbox/api'

export interface Drilldown {
  label: string
  query: FilterQuery
}

interface DrilldownState {
  pending: Drilldown | null
  set: (drilldown: Drilldown) => void
  /** Read and clear — a drill-down applies exactly once. */
  take: () => Drilldown | null
  clear: () => void
}

export const useDrilldownStore = create<DrilldownState>((set, get) => ({
  pending: null,
  set: (drilldown) => set({ pending: drilldown }),
  take: () => {
    const { pending } = get()
    if (pending) set({ pending: null })
    return pending
  },
  clear: () => set({ pending: null }),
}))
