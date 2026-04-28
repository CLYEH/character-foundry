import { useMutation } from '@tanstack/react-query'

import {
  createCheckpoint,
  type CreateCheckpointRequest,
  type CreateCheckpointResponse,
} from '@/api/endpoints/checkpoints'

export function useCreateCheckpoint(sessionId: string) {
  return useMutation<CreateCheckpointResponse, Error, CreateCheckpointRequest>({
    mutationFn: (body) => createCheckpoint(sessionId, body),
  })
}
