import { createBrowserRouter, RouterProvider } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'
import AuthLayout from '@/components/layout/AuthLayout'
import DashboardPage from '@/routes/dashboard/DashboardPage'
import LoginPage from '@/routes/login'
import NewCharacterPage from '@/routes/characters/new/NewCharacterPage'
import CreationSessionPage from '@/routes/characters/new/session/CreationSessionPage'
import CharacterDetailPage from '@/routes/characters/detail/CharacterDetailPage'
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
      { path: '/characters/new/session/:id', element: <CreationSessionPage /> },
      { path: '/characters/:id', element: <CharacterDetailPage /> },
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
