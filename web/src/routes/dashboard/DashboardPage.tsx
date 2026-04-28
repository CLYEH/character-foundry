import { Link } from 'react-router'

import type { Character } from '@/api/endpoints/characters'
import { useCharacterList } from '@/api/queries/useCharacterList'
import { CharacterGrid, CharacterGridEmpty, CharacterGridSkeleton } from '@/components/characters'
import { GenericErrorPage } from '@/components/composite/ErrorPage'
import { Button } from '@/components/ui/button'
import { AgentError } from '@/lib/agentError'
import { useAuthStore } from '@/stores/authStore'

export default function DashboardPage() {
  const userId = useAuthStore((s) => s.user?.id ?? null)
  const query = useCharacterList()

  return (
    <section className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">我的角色</h1>
        <Button asChild>
          <Link to="/characters/new">建立 Character</Link>
        </Button>
      </div>
      <DashboardBody
        userId={userId}
        isPending={query.isPending}
        isError={query.isError}
        error={query.error}
        items={query.data?.items ?? []}
        onRetry={() => {
          void query.refetch()
        }}
      />
    </section>
  )
}

interface DashboardBodyProps {
  userId: string | null
  isPending: boolean
  isError: boolean
  error: unknown
  items: Character[]
  onRetry: () => void
}

function DashboardBody({ userId, isPending, isError, error, items, onRetry }: DashboardBodyProps) {
  if (isPending) return <CharacterGridSkeleton />
  if (isError) {
    const agentError = AgentError.from(error)
    return (
      <GenericErrorPage
        description={agentError.message || '無法載入角色列表，請稍後再試。'}
        onRetry={onRetry}
      />
    )
  }
  if (items.length === 0) return <CharacterGridEmpty />
  return <CharacterGrid characters={items} currentUserId={userId} />
}
