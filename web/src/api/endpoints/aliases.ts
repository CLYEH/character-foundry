import { apiFetch } from '@/api/client'
import type { ReferenceImageUploadResponse } from '@/api/endpoints/reference-images'

/**
 * Backend `input_mode` discriminant (api-shape §5.3).
 *
 * Frontend computes this from which inputs are populated at submit time so
 * the worker can route to the right gpt-image-2 mode without re-deriving
 * the choice (text2image / image2image / inpaint / mixed).
 */
export type AliasInputMode = 'text' | 'image' | 'inpaint' | 'mixed'

export interface CreateAliasRequest {
  name: string
  input_mode: AliasInputMode
  freeform_note: string | null
  reference_image_ids: string[] | null
  /**
   * Server-side mask handle minted by `POST /v1/characters/{id}/aliases/masks`
   * (T-031). Frontend never inlines the bitmap into this body — masks ride
   * their own multipart upload because they can be large (>1 MB for
   * 1024×1024 PNGs) and `Content-Type: application/json` would force
   * base64-encoding the entire image.
   */
  mask: { mask_id: string } | null
}

export interface CreateAliasResponse {
  task_id: string
  alias_id: string
}

export function createAlias(
  characterId: string,
  body: CreateAliasRequest,
): Promise<CreateAliasResponse> {
  return apiFetch<CreateAliasResponse>(`/v1/characters/${characterId}/aliases`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * Mirror of the session-scoped reference upload, scoped to a character so
 * the alias-edit page (P-06) can attach references without inventing a
 * second creation session. Backend match lives in T-031; the request /
 * response shape stays identical to `POST /v1/creation-sessions/{id}/
 * reference-images` so the existing `useReferenceUpload` hook stays a
 * drop-in via its `uploader` parameter.
 */
export function uploadCharacterReference(
  characterId: string,
  file: File,
): Promise<ReferenceImageUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<ReferenceImageUploadResponse>(`/v1/characters/${characterId}/reference-images`, {
    method: 'POST',
    body: form,
  })
}

export interface UploadMaskResponse {
  mask_id: string
}

export function uploadMask(characterId: string, blob: Blob): Promise<UploadMaskResponse> {
  const form = new FormData()
  // Backend reads the part as `file`; suffix `.png` keeps the multipart
  // metadata accurate even if the blob's `.type` is empty (some browsers
  // omit it on canvas-derived blobs).
  form.append('file', blob, 'mask.png')
  return apiFetch<UploadMaskResponse>(`/v1/characters/${characterId}/aliases/masks`, {
    method: 'POST',
    body: form,
  })
}
