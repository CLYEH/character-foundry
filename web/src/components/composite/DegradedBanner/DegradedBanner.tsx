import { AlertTriangle } from 'lucide-react'

import { useMeta } from '@/api/queries/useMeta'
import type { DegradedService } from '@/api/endpoints/meta'
import { Alert, AlertDescription } from '@/components/ui/alert'

const SERVICE_LABELS: Record<string, string> = {
  'gpt-image-2': '圖像生成',
  'veo-3.1': '動作影片生成',
  reconciler: 'Prompt 最佳化',
}

function formatServiceMessage(entry: DegradedService): string {
  if (entry.message) return entry.message
  const label = SERVICE_LABELS[entry.service] ?? entry.service
  return `${label}暫時降級，品質可能稍降。`
}

export function DegradedBanner() {
  const { data } = useMeta()
  const degraded = data?.degraded_services ?? []
  if (degraded.length === 0) return null

  return (
    <Alert
      className="rounded-none border-x-0 border-t-0 border-amber-500/40 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
      data-testid="degraded-banner"
    >
      <AlertTriangle className="text-amber-600 dark:text-amber-400" aria-hidden />
      <AlertDescription className="text-amber-900/90 dark:text-amber-100/90">
        <ul className="flex flex-col gap-0.5">
          {degraded.map((entry) => (
            <li key={entry.service}>{formatServiceMessage(entry)}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  )
}
