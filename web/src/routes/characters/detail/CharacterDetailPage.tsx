import { useState } from 'react'
import { ArrowLeft, Download, Plus, Trash2 } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { ApiError } from '@/api/client'
import type { CharacterDetail } from '@/api/endpoints/characters'
import { useAliases } from '@/api/queries/useAliases'
import { useBaseMotions } from '@/api/queries/useBaseMotions'
import { AliasRow } from '@/components/aliases'
import {
  AliasEmptyState,
  BaseCard,
  BasePromptModal,
  IncompleteCharacterCard,
} from '@/components/characters'
import { GenericErrorPage, NotFoundPage } from '@/components/composite/ErrorPage'
import { MotionRow } from '@/components/motions'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useCharacterDetail } from '@/hooks/useCharacterDetail'
import { AgentError } from '@/lib/agentError'
import { useAuthStore } from '@/stores/authStore'

/**
 * P-05 Character Detail.
 *
 * Sprint 2 shipped Base + empty placeholders. T-037 lights up the
 * Aliases + Motions sections: alias list (owner can rename / delete /
 * navigate to the edit page) plus a motion strip per Base / Alias.
 * Motion mutations (preset + custom generation) stay disabled until
 * T-038 / T-039 land.
 */
export default function CharacterDetailPage() {
  const { id: characterId } = useParams<{ id: string }>()
  const query = useCharacterDetail(characterId)
  const [promptOpen, setPromptOpen] = useState(false)

  if (!characterId) {
    return <GenericErrorPage description="Character id 缺漏" />
  }

  if (query.isPending) {
    return <DetailSkeleton />
  }

  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return <NotFoundPage title="找不到這個角色" description="它可能已被刪除或 URL 寫錯了。" />
    }
    const agent = AgentError.from(query.error)
    return (
      <GenericErrorPage
        description={agent.message || '無法載入角色'}
        onRetry={() => {
          void query.refetch()
        }}
      />
    )
  }

  const { character } = query.data
  return (
    <CharacterDetailBody
      character={character}
      promptOpen={promptOpen}
      onOpenPrompt={() => setPromptOpen(true)}
      onClosePrompt={() => setPromptOpen(false)}
    />
  )
}

interface CharacterDetailBodyProps {
  character: CharacterDetail
  promptOpen: boolean
  onOpenPrompt: () => void
  onClosePrompt: () => void
}

function CharacterDetailBody({
  character,
  promptOpen,
  onOpenPrompt,
  onClosePrompt,
}: CharacterDetailBodyProps) {
  // Source the active user from the auth store rather than re-deriving
  // it via `useMe()`. The store value is set at login and persisted, so
  // a transient `/me` 5xx (which `useMe` would otherwise propagate as
  // `data === undefined`) doesn't downgrade an owner to a viewer
  // mid-session. Codex P2 on PR #62.
  const myUserId = useAuthStore((s) => s.user?.id)
  const isOwner = myUserId === character.owner.id

  return (
    <section className="flex flex-col gap-6">
      <Breadcrumb name={character.name} />

      <header className="flex flex-col gap-3 border-b border-border/60 pb-4 md:flex-row md:items-start md:justify-between">
        <div className="flex flex-col gap-1">
          <h1 data-testid="character-detail-name" className="text-2xl font-semibold">
            {character.name}
          </h1>
          <p className="text-sm text-muted-foreground">
            <span data-testid="character-detail-owner">by {character.owner.name}</span>
            <span className="mx-2">·</span>
            建立於{' '}
            <time dateTime={character.created_at}>
              {new Date(character.created_at).toLocaleDateString()}
            </time>
          </p>
        </div>
        <DetailActions />
      </header>

      {character.base === null ? (
        <IncompleteCharacterCard session={character.creation_session} />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <SectionHeader label="Base" />
            <BaseCard base={character.base} onViewPrompt={onOpenPrompt} />
            <BaseMotions baseId={character.base.id} isOwner={isOwner} />
          </section>

          <AliasesSection characterId={character.id} isOwner={isOwner} />

          <BasePromptModal isOpen={promptOpen} onClose={onClosePrompt} base={character.base} />
        </>
      )}
    </section>
  )
}

function BaseMotions({ baseId, isOwner }: { baseId: string; isOwner: boolean }) {
  const motionsQuery = useBaseMotions(baseId)
  const motions = motionsQuery.data?.items ?? []
  const errorMessage = motionsQuery.isError
    ? AgentError.from(motionsQuery.error).message || '載入失敗'
    : null
  return (
    <MotionRow
      parentType="base"
      parentId={baseId}
      motions={motions}
      isOwner={isOwner}
      isLoading={motionsQuery.isPending}
      errorMessage={errorMessage}
    />
  )
}

function AliasesSection({ characterId, isOwner }: { characterId: string; isOwner: boolean }) {
  const aliasesQuery = useAliases(characterId)

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <SectionHeader label="Aliases" />
        {(aliasesQuery.data?.items.length ?? 0) > 0 ? (
          isOwner ? (
            <Button asChild variant="secondary" size="sm" data-testid="alias-create-cta">
              <Link to={`/characters/${characterId}/aliases/new`}>
                <Plus className="size-3.5" aria-hidden />
                新增 Alias
              </Link>
            </Button>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button type="button" variant="secondary" size="sm" disabled>
                    <Plus className="size-3.5" aria-hidden />
                    新增 Alias
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>僅 owner 可操作</TooltipContent>
            </Tooltip>
          )
        ) : null}
      </div>

      {aliasesQuery.isPending ? (
        <Skeleton data-testid="aliases-skeleton" className="h-32 w-full" />
      ) : aliasesQuery.isError ? (
        <p
          role="alert"
          data-testid="aliases-error"
          className="rounded border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          {AgentError.from(aliasesQuery.error).message || '無法載入 alias 清單'}
        </p>
      ) : aliasesQuery.data.items.length === 0 ? (
        <AliasEmptyState characterId={characterId} isOwner={isOwner} />
      ) : (
        <ul data-testid="aliases-list" className="flex flex-col gap-3">
          {aliasesQuery.data.items.map((alias) => (
            <li key={alias.id}>
              <AliasRow alias={alias} characterId={characterId} isOwner={isOwner} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Breadcrumb({ name }: { name: string }) {
  return (
    <nav aria-label="導覽路徑" className="text-sm">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link to="/">
          <ArrowLeft className="size-4" aria-hidden />
          Dashboard
        </Link>
      </Button>
      <span className="mx-2 text-muted-foreground">›</span>
      <span className="text-muted-foreground">{name}</span>
    </nav>
  )
}

function SectionHeader({ label }: { label: string }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
      {label}
    </h2>
  )
}

function DetailActions() {
  // 刪除 / 下載 ZIP are out of scope for T-025 (Sprint 4) but we surface
  // disabled buttons with a tooltip so the layout matches P-05 wireframe.
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Button type="button" variant="outline" size="sm" disabled>
              <Download className="size-3.5" aria-hidden />
              下載 ZIP
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Sprint 4 會開放</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Button type="button" variant="outline" size="sm" disabled>
              <Trash2 className="size-3.5" aria-hidden />
              刪除
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Sprint 4 會開放</TooltipContent>
      </Tooltip>
    </div>
  )
}

function DetailSkeleton() {
  return (
    <section data-testid="character-detail-skeleton" className="flex flex-col gap-6">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-32 w-full" />
    </section>
  )
}
