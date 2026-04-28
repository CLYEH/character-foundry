import { Navigate, Outlet, useLocation } from 'react-router'

import { DegradedBanner } from '@/components/composite/DegradedBanner'
import { ErrorBoundary } from '@/components/composite/ErrorBoundary'
import { TopNav } from '@/components/composite/TopNav'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'

export default function AppLayout() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const location = useLocation()

  if (!accessToken) {
    const redirectBack = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?redirect_back=${redirectBack}`} replace />
  }

  return (
    <TooltipProvider>
      <div className="flex min-h-screen flex-col bg-background text-foreground">
        <TopNav />
        <DegradedBanner />
        <main className="flex-1 p-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
        <Toaster position="bottom-right" richColors closeButton />
      </div>
    </TooltipProvider>
  )
}
