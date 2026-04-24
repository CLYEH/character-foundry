import { Navigate, Outlet, useLocation } from 'react-router'

import { logout as logoutEndpoint } from '@/api/endpoints/auth'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'

export default function AppLayout() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const refreshToken = useAuthStore((s) => s.refreshToken)
  const clearAuth = useAuthStore((s) => s.logout)
  const location = useLocation()

  if (!accessToken) {
    const redirectBack = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?redirect_back=${redirectBack}`} replace />
  }

  const handleLogout = async () => {
    if (refreshToken) {
      try {
        await logoutEndpoint(refreshToken)
      } catch {
        // Best-effort revoke — clear client state regardless of server response.
      }
    }
    clearAuth()
  }

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex items-center justify-end border-b border-border p-4">
        <Button variant="outline" size="sm" onClick={handleLogout}>
          登出
        </Button>
      </header>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
