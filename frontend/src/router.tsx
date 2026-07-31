/**
 * Route registry. ORCHESTRATOR-OWNED — feature agents implement the lazily
 * imported page modules (default exports) but never edit this file.
 */

import { createBrowserRouter, Navigate } from 'react-router'

import { AppShell } from '@/components/layout/AppShell'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { RouteErrorPage } from '@/components/layout/RouteErrorPage'

export const router = createBrowserRouter([
  // Public auth routes
  { path: '/login', lazy: () => import('@/features/auth/pages/LoginPage') },
  { path: '/signup', lazy: () => import('@/features/auth/pages/SignupPage') },
  { path: '/accept-invite', lazy: () => import('@/features/auth/pages/AcceptInvitePage') },
  { path: '/forgot-password', lazy: () => import('@/features/auth/pages/ForgotPasswordPage') },
  { path: '/reset-password', lazy: () => import('@/features/auth/pages/ResetPasswordPage') },

  {
    element: <RequireAuth />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: '/onboarding', lazy: () => import('@/features/auth/pages/OnboardingPage') },
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/inbox" replace /> },
          { path: '/inbox/:conversationId?', lazy: () => import('@/features/inbox/pages/InboxPage') },
          { path: '/contacts', lazy: () => import('@/features/contacts/pages/ContactsPage') },
          {
            path: '/contacts/:contactId',
            lazy: () => import('@/features/contacts/pages/ContactDetailPage'),
          },
          { path: '/knowledge', lazy: () => import('@/features/knowledge/pages/KnowledgePage') },
          {
            path: '/knowledge/sources/:sourceId',
            lazy: () => import('@/features/knowledge/pages/SourceDetailPage'),
          },
          {
            path: '/knowledge/articles',
            lazy: () => import('@/features/knowledge/pages/ArticlesPage'),
          },
          {
            path: '/knowledge/search',
            lazy: () => import('@/features/knowledge/pages/SearchPlaygroundPage'),
          },
          { path: '/ai', lazy: () => import('@/features/ai/pages/AiOverviewPage') },
          { path: '/ai/providers', lazy: () => import('@/features/ai/pages/ProvidersPage') },
          { path: '/ai/agents', lazy: () => import('@/features/ai/pages/AgentsPage') },
          { path: '/ai/agents/:agentId', lazy: () => import('@/features/ai/pages/AgentBuilderPage') },
          { path: '/ai/approvals', lazy: () => import('@/features/ai/pages/ApprovalsPage') },
          { path: '/ai/runs', lazy: () => import('@/features/ai/pages/RunsPage') },
          { path: '/ai/runs/:runId', lazy: () => import('@/features/ai/pages/RunDetailPage') },
          { path: '/automation', lazy: () => import('@/features/automation/pages/AutomationPage') },
          { path: '/tours', lazy: () => import('@/features/tours/pages/ToursPage') },
          { path: '/tours/:tourId', lazy: () => import('@/features/tours/pages/TourEditorPage') },
          { path: '/reports', lazy: () => import('@/features/reports/pages/ReportsPage') },
          { path: '/settings', lazy: () => import('@/features/settings/pages/SettingsLayout') },
          {
            path: '/settings/:section',
            lazy: () => import('@/features/settings/pages/SettingsLayout'),
          },
        ],
      },
    ],
  },
  { path: '*', lazy: () => import('@/components/layout/NotFoundPage') },
])
