import type { PresetMotionType } from '@/api/endpoints/motions'

/**
 * Short labels rendered inside a 64×64 motion cell. Long-form names
 * wrap in the strip layout, so the cell uses the abbreviated wireframe
 * spelling (planning/ux/wireframes.md P-05 / Flow C).
 */
export const PRESET_LABELS: Record<PresetMotionType, string> = {
  preset_wave: '招手',
  preset_nod: '點頭',
  preset_gesture: '手勢',
  preset_happy: '開心',
  preset_idle: '待機',
}

/**
 * Canonical preset motion names (F-20). Used as the `name` field in
 * `POST /v1/{bases|aliases}/{id}/motions` so the persisted motion
 * matches the product spec across UI / API / agent surfaces.
 */
export const PRESET_NAMES: Record<PresetMotionType, string> = {
  preset_wave: '招手歡迎',
  preset_nod: '點頭說明',
  preset_gesture: '手勢指引',
  preset_happy: '開心回應',
  preset_idle: '靜置待機',
}
