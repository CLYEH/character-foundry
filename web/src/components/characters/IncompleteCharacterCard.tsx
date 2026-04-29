import { ArrowLeft, ArrowRight } from 'lucide-react'
import { Link } from 'react-router'

import type { CharacterDetailCreationSessionRef } from '@/api/endpoints/characters'
import { Button } from '@/components/ui/button'

export interface IncompleteCharacterCardProps {
  /**
   * Embedded ref from `CharacterDetail.creation_session`. `null` lands
   * here in the FK-orphan case — `Character.creation_session_id` is
   * `ON DELETE SET NULL`, so a session row deleted out from under a
   * Base-less character leaves the column null and the serializer
   * emits `creation_session: null` even though `base` is also null.
   * api-shape §6.2 marks this combo as the "abnormal" state.
   */
  session: CharacterDetailCreationSessionRef | null
}

/**
 * Hero card shown on the Character Detail page when no Base has been
 * confirmed (`base === null`). Drives the resume flow when the
 * underlying `creation_session` is still `in_progress`; otherwise tells
 * the user the session is abandoned (no resume — abandoned sessions
 * aren't meant to continue, per user-flows §4.1) or surfaces the
 * abnormal "no session at all" state with a back-to-dashboard
 * fallback.
 */
export function IncompleteCharacterCard({ session }: IncompleteCharacterCardProps) {
  if (session?.status === 'in_progress') {
    return (
      <Card testId="character-detail-resume-in-progress">
        <CardTitle>此角色尚未確立 Base</CardTitle>
        <CardBody>從上次離開的地方繼續挑選 Base，或建立新的 checkpoint。</CardBody>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button asChild size="sm">
            <Link to={`/characters/new/session/${session.id}`}>
              繼續建立
              <ArrowRight className="size-4" aria-hidden />
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/">
              <ArrowLeft className="size-4" aria-hidden />回 Dashboard
            </Link>
          </Button>
        </div>
      </Card>
    )
  }

  if (session?.status === 'abandoned') {
    return (
      <Card testId="character-detail-session-abandoned">
        <CardTitle>此 session 已被放棄</CardTitle>
        <CardBody>無法從這個 session 繼續，請從 Dashboard 開新角色。</CardBody>
        <Button asChild variant="outline" size="sm">
          <Link to="/">
            <ArrowLeft className="size-4" aria-hidden />回 Dashboard
          </Link>
        </Button>
      </Card>
    )
  }

  // Defensive fallback: base === null && creation_session === null is
  // an abnormal state per api-shape §6.2 (the serializer should always
  // populate the ref when base is null). Keep the legacy inline error
  // surface so the page still renders something usable.
  return (
    <Card testId="character-detail-no-base">
      <CardTitle>此角色尚未確立 Base</CardTitle>
      <CardBody>Creation Session 狀態異常。回 Dashboard 重新進入流程。</CardBody>
      <Button asChild variant="outline" size="sm">
        <Link to="/">
          <ArrowLeft className="size-4" aria-hidden />回 Dashboard
        </Link>
      </Button>
    </Card>
  )
}

function Card({ children, testId }: { children: React.ReactNode; testId: string }) {
  return (
    <div
      data-testid={testId}
      className="flex min-h-[40vh] flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border/60 bg-muted/30 px-6 py-10 text-center"
    >
      {children}
    </div>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return <p className="text-base font-medium">{children}</p>
}

function CardBody({ children }: { children: React.ReactNode }) {
  return <p className="max-w-md text-sm text-muted-foreground">{children}</p>
}
