import { apiFetch } from '@/api/client'

export interface ReferenceImageUploadResponse {
  reference_image_id: string
  /**
   * Short-lived signed URL the backend mints for previews. We carry it
   * locally for the lifetime of the creation session — only the
   * `reference_image_id` ever travels back to the server in subsequent
   * checkpoint requests.
   */
  url: string
}

export function uploadReferenceImage(
  sessionId: string,
  file: File,
): Promise<ReferenceImageUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return apiFetch<ReferenceImageUploadResponse>(
    `/v1/creation-sessions/${sessionId}/reference-images`,
    {
      method: 'POST',
      body: form,
    },
  )
}
