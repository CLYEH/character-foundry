import { useState } from 'react'
import { ArrowLeft, Download, Trash2 } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { ApiError } from '@/api/client'
import type { CharacterDetail } from '@/api/endpoints/characters'
import {
  AliasEmptyState,
  BaseCard,
  BasePromptModal,
  IncompleteCharacterCard,
  MotionEmptyStrip,
} from '@/components/characters'
import { GenericErrorPage, NotFoundPage } from '@/components/composite/ErrorPage'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useCharacterDetail } from '@/hooks/useCharacterDetail'
import { AgentError } from '@/lib/agentError'

/**
 * P-05 Character Detail (Sprint 2 cut).
 *
 * Renders Base + empty placeholders for Aliases / Motions. When no Base
 * has been confirmed yet, the page swaps the body for
 * `IncompleteCharacterCard` which drives Resume / Abandoned states off
 * `character.creation_session` (api-shape §6.2).
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
          </section>

          <section className="flex flex-col gap-3">
            <SectionHeader label="Aliases" />
            <AliasEmptyState />
          </section>

          <section className="flex flex-col gap-3">
            <SectionHeader label="Motions" />
            <MotionEmptyStrip />
          </section>

          <BasePromptModal isOpen={promptOpen} onClose={onClosePrompt} base={character.base} />
        </>
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
