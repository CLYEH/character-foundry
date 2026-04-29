import { apiFetch } from '@/api/client'
import type { MenuSelections } from '@/constants/menu_options'

/**
 * `POST /v1/prompt/preview` — see `planning/backend/api-shape.md` §5.6.
 * Pure read: combines fixed platform constraints, menu fragments, and the
 * LLM-reconciled English note into the final model prompt without firing
 * a generation. Backend caches the reconciler call in Redis (24h TTL) so
 * reopens of the same input are cheap.
 *
 * Request shape is a discriminated union by `mode` (T-035 backend contract).
 * Hand-coded here so frontend Wave A can land in parallel with backend;
 * once T-035 merges, run `pnpm typegen` and replace the union with the
 * generated types from `src/api/generated/openapi-types.ts`.
 */
export type PromptPreviewMode = 'create_base' | 'create_alias' | 'create_motion'

/** Mask reference (T-035 `MaskInput`). Mask payload is uploaded out-of-band
 *  and referenced by id; preview only needs to know one is present. */
export interface MaskInput {
  mask_id: string
}

export type AliasInputMode = 'text' | 'image' | 'inpaint' | 'mixed'

export type MotionParentType = 'base' | 'alias'

/** `mode='create_base'`. Used by the Creation Session page (T-022 / T-023).
 *  `base_checkpoint_id` is set when the user picks a checkpoint as the
 *  remix anchor — the worker reconciles with `has_reference_image=True`
 *  sourced from that checkpoint, and T-035 mirrors the signal in preview
 *  so the modal renders a faithful prompt (closes STATUS.md S2-5). */
export interface PromptPreviewBaseRequest {
  mode: 'create_base'
  menu_selections?: MenuSelections | null
  freeform_note?: string | null
  reference_image_ids?: string[] | null
  base_checkpoint_id?: string | null
}

/** `mode='create_alias'`. Used by the Alias edit page (T-036). */
export interface PromptPreviewAliasRequest {
  mode: 'create_alias'
  character_id: string
  input_mode: AliasInputMode
  menu_selections?: MenuSelections | null
  freeform_note?: string | null
  reference_image_ids?: string[] | null
  mask?: MaskInput | null
}

/** `mode='create_motion'`. Used by the Custom Motion modal (T-039); preset
 *  motions also flow through here so the modal can show the platform
 *  template. `motion_type` is `'preset_*'` or `'custom'`. */
export interface PromptPreviewMotionRequest {
  mode: 'create_motion'
  parent_type: MotionParentType
  parent_id: string
  motion_type: string
  description?: string | null
}

export type PromptPreviewRequest =
  | PromptPreviewBaseRequest
  | PromptPreviewAliasRequest
  | PromptPreviewMotionRequest

/** Mode-specific blocks (T-035 §Response schema) come back as optional
 *  fields rather than a separate response per mode — the consumer already
 *  knows which mode it asked for and narrows accordingly. */
export interface DerivedFromBase {
  base_id: string
  base_image_url: string
}

export interface MotionParent {
  type: MotionParentType
  id: string
  image_url: string
}

export type MotionTemplateUsed = 'custom_reconciled' | (string & {})

export interface PromptPreviewResponse {
  platform_constraints: string
  menu_fragments: string[]
  reconciled_note_en: string
  final_prompt: string
  /** Present when `mode='create_alias'`. */
  derived_from?: DerivedFromBase
  /** Present when `mode='create_motion'`. */
  parent?: MotionParent
  /** Present when `mode='create_motion'`. `'preset_*'` indicates the
   *  preset template was used as-is (no reconciler call). */
  motion_template_used?: MotionTemplateUsed
}

export function previewPrompt(body: PromptPreviewRequest): Promise<PromptPreviewResponse> {
  return apiFetch<PromptPreviewResponse>('/v1/prompt/preview', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
