import { api } from '@/api/client'
import type { MembershipSummary, User } from '@/stores/auth'

export interface TokenResponse {
  access_token: string
  expires_in: number
  user: User
}

export interface MeResponse {
  user: User
  memberships: MembershipSummary[]
}

export const authApi = {
  signup: (body: { email: string; name: string; password: string }) =>
    api.post<TokenResponse>('/api/v1/auth/signup', body, { noRetry: true }),
  login: (body: { email: string; password: string }) =>
    api.post<TokenResponse>('/api/v1/auth/login', body, { noRetry: true }),
  logout: () => api.post('/api/v1/auth/logout', undefined, { noRetry: true }),
  requestPasswordReset: (email: string) =>
    api.post('/api/v1/auth/password-reset', { email }, { noRetry: true }),
  confirmPasswordReset: (token: string, new_password: string) =>
    api.post('/api/v1/auth/password-reset/confirm', { token, new_password }, { noRetry: true }),
  me: () => api.get<MeResponse>('/api/v1/me'),
  acceptInvite: (token: string) => api.post<MembershipSummary>('/api/v1/invitations/accept', { token }),
  createWorkspace: (name: string) => api.post<{ id: string }>('/api/v1/workspaces', { name }),
}
