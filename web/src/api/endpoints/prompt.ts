import { apiFetch } from '@/api/client'
import type { MenuSelections } from '@/constants/menu_options'

/**
 * `POST /v1/prompt/preview` — see `planning/backend/api-shape.md` §5.6 and
 * functional-scope F-04b. Pure read: combines fixed platform constraints,
 * menu fragments, and the LLM-reconciled English note into the final
 * `gpt-image-2` prompt without firing a generation. Backend caches the
 * reconciler call in Redis (24h TTL) so reopens of the same input are cheap.
 */
export type PromptPreviewMode = 'create_base' | 'create_alias' | 'create_motion'

export interface PromptPreviewRequest {
  mode: PromptPreviewMode
  menu_selections?: MenuSelections | null
  freeform_note?: string | null
  reference_image_ids?: string[] | null
  /** PNG mask payload for inpaint (Alias edit). Phase 1 session pages don't
   *  surface this yet — kept on the wire so M-01 stays parameter-stable. */
  mask?: string | null
}

export interface PromptPreviewResponse {
  platform_constraints: string
  menu_fragments: string[]
  reconciled_note_en: string
  final_prompt: string
}

export function previewPrompt(body: PromptPreviewRequest): Promise<PromptPreviewResponse> {
  return apiFetch<PromptPreviewResponse>('/v1/prompt/preview', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
