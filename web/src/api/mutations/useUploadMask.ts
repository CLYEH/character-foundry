import { useMutation } from '@tanstack/react-query'

import { uploadMask, type UploadMaskResponse } from '@/api/endpoints/aliases'

export function useUploadMask(characterId: string) {
  return useMutation<UploadMaskResponse, Error, Blob>({
    mutationFn: (blob) => uploadMask(characterId, blob),
  })
}
