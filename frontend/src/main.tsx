import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { Toaster } from 'sonner'

import { bootstrapLocale } from '@/i18n/bootstrap'
import { router } from '@/router'

import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status
        if (status && status >= 400 && status < 500) return false
        return failureCount < 2
      },
    },
  },
})

// The catalog has to be in memory before React paints, or the app renders once
// in English and repaints — a visible flicker, and a full layout shift in RTL
// where the whole page changes direction.
//
// A `.then` rather than a top-level `await`: the build targets browsers older
// than top-level await (chrome87 / safari14), and raising that floor is a
// browser-support decision, not something an i18n change should do quietly.
// `bootstrapLocale` never rejects, so there is no unhandled path here.
void bootstrapLocale().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster richColors position="top-right" />
      </QueryClientProvider>
    </StrictMode>
  )
})
