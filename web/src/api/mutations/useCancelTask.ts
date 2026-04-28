import { useMutation } from '@tanstack/react-query'

import { cancelTask, type CancelTaskResponse } from '@/api/endpoints/tasks'

export function useCancelTask() {
  return useMutation<CancelTaskResponse, Error, string>({
    mutationFn: (taskId) => cancelTask(taskId),
  })
}
