import type { AspectRatio } from '@/api/endpoints/checkpoints'

/**
 * Aspect-ratio dropdown options for the creation session input panels (T-047).
 * Values match the OpenAI gpt-image legal `size` enum mapping in the backend
 * `app.ai.gpt_image_2._SIZE_MAP`. Kept as a tiny module-level constant rather
 * than enum/i18n indirection — it only renders in two places and the labels
 * carry the dimension hint right there.
 */
export const ASPECT_RATIO_OPTIONS: ReadonlyArray<{ value: AspectRatio; label_zh: string }> = [
  { value: '2:3', label_zh: '直立 2:3 (1024×1536)' },
  { value: '1:1', label_zh: '正方形 1:1 (1024×1024)' },
  { value: '3:2', label_zh: '橫向 3:2 (1536×1024)' },
  { value: 'auto', label_zh: '自動 (由模型決定)' },
]
