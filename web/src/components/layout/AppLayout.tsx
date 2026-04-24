import { Outlet } from 'react-router'

export default function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
