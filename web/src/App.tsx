import { createBrowserRouter, RouterProvider } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import AuthLayout from '@/components/layout/AuthLayout'
import DashboardPage from '@/routes/dashboard/DashboardPage'
import LoginPage from '@/routes/login'
import NewCharacterPage from '@/routes/characters/new/NewCharacterPage'
import NotFoundPage from '@/routes/not-found'
import { queryClient } from '@/api/queryClient'

const router = createBrowserRouter([
  {
    element: <AuthLayout />,
    children: [{ path: '/login', element: <LoginPage /> }],
  },
  {
    element: <AppLayout />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/characters/new', element: <NewCharacterPage /> },
      // /characters/new/session/:id lands in T-022 (CreationSessionPage),
      // /characters/:id lands in T-025 (CharacterDetailPage). Until those
      // tickets ship, links to those paths fall through to the catch-all
      // below by design — see STATUS.md Sprint 2.
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
