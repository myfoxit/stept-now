import { create } from 'zustand'

export interface User {
  id: string
  email: string
  name: string
  avatar_url?: string | null
}

export interface WorkspaceSummary {
  id: string
  name: string
  slug: string
  logo_url?: string | null
  settings: Record<string, unknown>
}

export interface MembershipSummary {
  id: string
  role: string
  is_available: boolean
  workspace: WorkspaceSummary
  permissions: string[]
}

interface AuthState {
  accessToken: string | null
  user: User | null
  memberships: MembershipSummary[]
  workspaceId: string | null
  /** true once the initial /me (or refresh) attempt finished */
  bootstrapped: boolean
  setAccessToken: (token: string | null) => void
  setSession: (user: User, memberships: MembershipSummary[]) => void
  setWorkspace: (workspaceId: string) => void
  setBootstrapped: () => void
  clear: () => void
}

const WORKSPACE_KEY = 'stept-workspace'

function readStoredWorkspace(): string | null {
  try {
    return globalThis.localStorage?.getItem(WORKSPACE_KEY) ?? null
  } catch {
    return null
  }
}

function storeWorkspace(id: string): void {
  try {
    globalThis.localStorage?.setItem(WORKSPACE_KEY, id)
  } catch {
    /* private mode / test envs */
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  memberships: [],
  workspaceId: readStoredWorkspace(),
  bootstrapped: false,
  setAccessToken: (accessToken) => set({ accessToken }),
  setSession: (user, memberships) => {
    let workspaceId = get().workspaceId
    if (!workspaceId || !memberships.some((m) => m.workspace.id === workspaceId)) {
      workspaceId = memberships[0]?.workspace.id ?? null
      if (workspaceId) storeWorkspace(workspaceId)
    }
    set({ user, memberships, workspaceId, bootstrapped: true })
  },
  setWorkspace: (workspaceId) => {
    storeWorkspace(workspaceId)
    set({ workspaceId })
  },
  setBootstrapped: () => set({ bootstrapped: true }),
  clear: () => {
    set({ accessToken: null, user: null, memberships: [], bootstrapped: true })
  },
}))

/** Current membership (role + permissions) for the active workspace. */
export function useCurrentMembership(): MembershipSummary | null {
  const { memberships, workspaceId } = useAuthStore()
  return memberships.find((m) => m.workspace.id === workspaceId) ?? null
}

export function useHasPerm(perm: string): boolean {
  const membership = useCurrentMembership()
  return membership?.permissions.includes(perm) ?? false
}

export function currentWorkspaceId(): string {
  const id = useAuthStore.getState().workspaceId
  if (!id) throw new Error('No active workspace')
  return id
}
