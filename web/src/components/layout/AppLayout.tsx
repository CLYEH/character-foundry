import { Navigate, Outlet, useLocation } from 'react-router'

import { DegradedBanner } from '@/components/composite/DegradedBanner'
import { TopNav } from '@/components/composite/TopNav'
import { useAuthStore } from '@/stores/authStore'

export default function AppLayout() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const location = useLocation()

  if (!accessToken) {
    const redirectBack = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?redirect_back=${redirectBack}`} replace />
  }

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <TopNav />
      <DegradedBanner />
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
