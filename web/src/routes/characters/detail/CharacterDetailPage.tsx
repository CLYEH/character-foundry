import { useState } from 'react'
import { ArrowLeft, Download, Trash2 } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { ApiError } from '@/api/client'
import type { CharacterDetail } from '@/api/endpoints/characters'
import {
  AliasEmptyState,
  BaseCard,
  BasePromptModal,
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
 * Renders Base + empty placeholders for Aliases / Motions. The
 * `base === null` branch is a deliberate fallback inline error — Sprint 2
 * does not redirect to the unfinished session yet (T-027 will own that
 * once `creation_session` lands on the DTO).
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
      return (
        <NotFoundPage
          title="找不到這個角色"
          description="它可能已被刪除或 URL 寫錯了。"
        />
      )
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
        <BaseMissingFallback />
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

function BaseMissingFallback() {
  // Sprint 2 fallback: T-027 will replace this branch with a Resume CTA
  // once `CharacterDetail.creation_session` lands. Until then we tell the
  // user the character isn't ready and route them back to Dashboard so
  // they can re-enter the session through the in-progress card (T-027).
  return (
    <div
      data-testid="character-detail-no-base"
      className="flex min-h-[40vh] flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center"
    >
      <p className="text-base font-medium">此角色尚未確立 Base</p>
      <p className="max-w-md text-sm text-muted-foreground">
        Creation Session 尚未完成。回 Dashboard 繼續挑選或建立 Base 圖。
      </p>
      <Button asChild variant="outline" size="sm">
        <Link to="/">
          <ArrowLeft className="size-4" aria-hidden />
          回 Dashboard
        </Link>
      </Button>
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
