import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deleteMotion, type MotionParentRef } from '@/api/endpoints/motions'
import { aliasMotionsQueryKey } from '@/api/queries/useAliasMotions'
import { baseMotionsQueryKey } from '@/api/queries/useBaseMotions'
import { useAuthStore } from '@/stores/authStore'

/**
 * Soft-delete a motion. Invalidates the parent's motion list so the
 * row's slot flips back to empty (preset) or the custom strip drops
 * the cell.
 */
export function useDeleteMotion(parent: MotionParentRef) {
  const qc = useQueryClient()
  const userId = useAuthStore((s) => s.user?.id)
  return useMutation<void, Error, { motionId: string }>({
    mutationFn: ({ motionId }) => deleteMotion(motionId),
    onSuccess: () => {
      const queryKey =
        parent.type === 'alias'
          ? aliasMotionsQueryKey(userId, parent.id)
          : baseMotionsQueryKey(userId, parent.id)
      void qc.invalidateQueries({ queryKey })
    },
  })
}
