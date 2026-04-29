import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import type Konva from 'konva'
import { Eraser, Paintbrush, Trash2 } from 'lucide-react'
import { Image as KonvaImage, Layer, Line, Stage } from 'react-konva'

import { Button } from '@/components/ui/button'

export type BrushTool = 'brush' | 'eraser'

export interface MaskPayload {
  blob: Blob
  coveragePercent: number
}

export interface InpaintCanvasHandle {
  /**
   * Resolves once any pending mask export (the async `canvas.toBlob`
   * fired from the latest stroke-end / clear / enable-toggle) has
   * settled. The parent must await this before reading its `maskPayload`
   * state at submit time — otherwise a fast user can finish a stroke
   * and click submit in the microsecond window before `toBlob` fires,
   * silently downgrading `input_mode` to non-inpaint (Codex P1 round 5).
   */
  flushPendingExport(): Promise<void>
}

export interface InpaintCanvasProps {
  baseImageUrl: string
  /**
   * When false the canvas is read-only — strokes are preserved internally
   * (so toggling back doesn't lose work) but pointer events drop and the
   * parent panel mutes mask coverage.
   */
  enabled: boolean
  /**
   * Fires with the latest mask blob whenever strokes settle. Sends `null`
   * when the canvas has no strokes yet, when the user clears them, or when
   * the canvas is disabled — so the parent never has to reason about
   * stale handles.
   */
  onMaskChange: (mask: MaskPayload | null) => void
}

interface Stroke {
  tool: BrushTool
  size: number
  /** Flat `[x0, y0, x1, y1, ...]` array — the shape Konva's Line expects. */
  points: number[]
}

const DEFAULT_BRUSH_SIZE = 40
const MIN_BRUSH = 8
const MAX_BRUSH = 120

const MASK_DISPLAY_OPACITY = 0.45
const MASK_EXPORT_OPACITY = 1

/**
 * P-06 mask drawing surface. Two Konva layers so the displayed image stays
 * legible (mask painted on top at 45% opacity) while the exported PNG is
 * just the alpha mask. Stage runs at the base image's natural pixel size
 * and is CSS-scaled for layout — Konva resolves pointer positions through
 * `getBoundingClientRect`, so coordinates remain in natural-image space
 * regardless of how the parent sizes the wrapper.
 *
 * Eraser strokes use `destination-out` composite so they punch through
 * any earlier brush strokes inside the same mask layer rather than just
 * painting transparent pixels (which on a still-empty mask would be a
 * no-op).
 */
export const InpaintCanvas = forwardRef<InpaintCanvasHandle, InpaintCanvasProps>(
  function InpaintCanvas({ baseImageUrl, enabled, onMaskChange }, ref) {
    const [image, setImage] = useState<HTMLImageElement | null>(null)
    const [strokes, setStrokes] = useState<Stroke[]>([])
    const [tool, setTool] = useState<BrushTool>('brush')
    const [brushSize, setBrushSize] = useState(DEFAULT_BRUSH_SIZE)
    const isDrawingRef = useRef(false)
    const maskLayerRef = useRef<Konva.Layer | null>(null)
    // Memoise the latest sent payload's kind so we don't double-fire
    // `onMaskChange(null)` after the parent already saw it. Initial value
    // `'null'` (string sentinel) suppresses a redundant clear on first
    // mount when strokes are empty — the parent already starts at null.
    const lastSentRef = useRef<'null' | 'mask'>('null')
    // Promise of the in-flight `canvas.toBlob` callback. The parent's
    // submit gate (`flushPendingExport`) awaits this so it can never
    // read a stale `maskPayload` between stroke-end and the async
    // `onMaskChange` firing.
    const pendingExportRef = useRef<Promise<void>>(Promise.resolve())

    // Tracks an image-load failure so the component can render a
    // recoverable error state instead of hanging on "載入中" forever
    // (Codex P2 round 6 — bad/expired/CDN-down URL).
    const [imageLoadFailed, setImageLoadFailed] = useState(false)

    // Load the base image — `new Image()` rather than `useImage` from
    // `use-image` avoids a peer dep just for the convenience hook. CORS is
    // unset on purpose because in-product these are same-origin signed URLs;
    // setting `crossOrigin = 'anonymous'` would tank tests where the URL is
    // a fake string.
    useEffect(() => {
      if (!baseImageUrl) return
      let cancelled = false
      // Reset error state for new URLs so a retry (parent passes a fresh
      // signed URL) can recover.
      setImageLoadFailed(false)
      const img = new window.Image()
      img.onload = () => {
        if (cancelled) return
        setImage(img)
      }
      img.onerror = () => {
        if (cancelled) return
        setImageLoadFailed(true)
      }
      img.src = baseImageUrl
      return () => {
        cancelled = true
        // Detach the handlers so a late decode doesn't keep the closure
        // (and thus the previous setters) reachable.
        img.onload = null
        img.onerror = null
      }
    }, [baseImageUrl])

    // `strokesRef` mirrors `strokes` synchronously so the imperative
    // `exportMaskNow` (called from endStroke / handleClear) reads the
    // latest array without a render-time closure capturing stale state.
    const strokesRef = useRef<Stroke[]>([])
    // Track the latest in-flight `toBlob` callback so a new export request
    // (or unmount) can abort the previous one — the late blob would
    // otherwise land in the parent as a stale mask handle.
    const exportAbortRef = useRef<{ aborted: boolean } | null>(null)

    // Imperative export: runs on stroke completion (pointerup / leave /
    // cancel) and on canvas clear, NOT on every pointer-move. The previous
    // implementation re-ran `toCanvas + getImageData + toBlob` on every
    // setStrokes and stalled drawing on 1K+ images / mobile (Codex P2
    // round 2).
    const exportMaskNow = useCallback(() => {
      if (!enabled) return
      const layer = maskLayerRef.current
      if (!layer || !image) return
      if (strokesRef.current.length === 0) {
        if (lastSentRef.current === 'null') return
        onMaskChange(null)
        lastSentRef.current = 'null'
        return
      }

      layer.opacity(MASK_EXPORT_OPACITY)
      const canvas = layer.toCanvas({ pixelRatio: 1 })
      layer.opacity(MASK_DISPLAY_OPACITY)

      const ctx = canvas.getContext('2d')
      let coverage = 0
      if (ctx) {
        try {
          const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
          let masked = 0
          for (let i = 3; i < data.length; i += 4) {
            if (data[i] > 0) masked += 1
          }
          coverage = (masked / (canvas.width * canvas.height)) * 100
        } catch {
          // jsdom canvas may not back getImageData; tests skip the export
          // path entirely via the mocked react-konva module.
        }
      }

      // Abort any prior pending toBlob so its late callback doesn't race
      // a newer export with stale data.
      if (exportAbortRef.current) exportAbortRef.current.aborted = true
      const guard = { aborted: false }
      exportAbortRef.current = guard

      // Wrap the toBlob callback in a Promise so the parent's submit gate
      // can `await flushPendingExport()` and never read a stale
      // `maskPayload` between stroke-end and the async callback firing.
      pendingExportRef.current = new Promise<void>((resolve) => {
        canvas.toBlob((blob) => {
          if (!guard.aborted && blob) {
            onMaskChange({ blob, coveragePercent: coverage })
            lastSentRef.current = 'mask'
          }
          resolve()
        }, 'image/png')
      })
    }, [enabled, image, onMaskChange])

    useImperativeHandle(
      ref,
      () => ({
        flushPendingExport: () => pendingExportRef.current,
      }),
      [],
    )

    // Disabling the canvas blanks the parent's mask handle. We don't drop
    // the strokes here (toggling back on should restore them), but the
    // parent must treat the mask as absent for input-mode resolution.
    useEffect(() => {
      if (enabled) {
        // Re-enable: flush whatever strokes exist now (covers
        // toggle-off-then-on without losing the painted mask).
        exportMaskNow()
        return
      }
      if (lastSentRef.current === 'null') return
      if (exportAbortRef.current) exportAbortRef.current.aborted = true
      onMaskChange(null)
      lastSentRef.current = 'null'
    }, [enabled, exportMaskNow, onMaskChange])

    // Image load: the first time we have dimensions, sync the parent so
    // an empty mask reads as `null` and the layer is ready to export. No
    // strokes can exist yet, so this is a one-time idempotent emit.
    useEffect(() => {
      if (!enabled || !image) return
      exportMaskNow()
      // Only fire on initial image arrival, not every exportMaskNow churn.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, image])

    // Cancel any in-flight toBlob if the component unmounts.
    useEffect(() => {
      return () => {
        if (exportAbortRef.current) exportAbortRef.current.aborted = true
      }
    }, [])

    // ---- Pointer handlers --------------------------------------------------

    const handlePointerDown = useCallback(
      (event: Konva.KonvaEventObject<PointerEvent>) => {
        if (!enabled) return
        const stage = event.target.getStage()
        const pointer = stage?.getRelativePointerPosition()
        if (!pointer) return
        isDrawingRef.current = true
        const next: Stroke[] = [
          ...strokesRef.current,
          { tool, size: brushSize, points: [pointer.x, pointer.y] },
        ]
        strokesRef.current = next
        setStrokes(next)
      },
      [brushSize, enabled, tool],
    )

    const handlePointerMove = useCallback((event: Konva.KonvaEventObject<PointerEvent>) => {
      if (!isDrawingRef.current) return
      const stage = event.target.getStage()
      const pointer = stage?.getRelativePointerPosition()
      if (!pointer) return
      const prev = strokesRef.current
      if (prev.length === 0) return
      const last = prev[prev.length - 1]
      const updated: Stroke = { ...last, points: [...last.points, pointer.x, pointer.y] }
      const next = [...prev.slice(0, -1), updated]
      strokesRef.current = next
      setStrokes(next)
    }, [])

    // `endStroke` covers every way a pointer interaction can finish:
    // `pointerup` (normal release inside the stage), `pointerleave`
    // (release while the cursor exits the stage), and `pointercancel`
    // (touch interruption — phone call, scroll gesture takeover). Without
    // the leave/cancel paths, `isDrawingRef` would stay `true` and the
    // next pointer move would extend the previous stroke without a fresh
    // press. Export only fires here (stroke complete), not per pointer-move.
    const endStroke = useCallback(() => {
      if (!isDrawingRef.current) return
      isDrawingRef.current = false
      exportMaskNow()
    }, [exportMaskNow])

    const handleClear = useCallback(() => {
      strokesRef.current = []
      setStrokes([])
      exportMaskNow()
    }, [exportMaskNow])

    // ---- Render ------------------------------------------------------------

    const naturalW = image?.naturalWidth ?? 0
    const naturalH = image?.naturalHeight ?? 0

    const stageStyle = useMemo<CSSProperties>(() => {
      if (!naturalW || !naturalH) return {}
      return {
        width: '100%',
        maxWidth: `${naturalW}px`,
        aspectRatio: `${naturalW} / ${naturalH}`,
      }
    }, [naturalW, naturalH])

    if (imageLoadFailed) {
      return (
        <div
          data-testid="inpaint-canvas-error"
          className="flex h-64 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-destructive/50 bg-destructive/5 p-4 text-center text-sm text-destructive"
        >
          <span>Base 圖讀取失敗</span>
          <span className="text-xs text-muted-foreground">
            連結可能失效，請取消 Inpaint 後回 Character 詳情重新整理。
          </span>
        </div>
      )
    }

    if (!image) {
      return (
        <div
          data-testid="inpaint-canvas-loading"
          className="flex h-64 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground"
        >
          載入 Base 圖中…
        </div>
      )
    }

    return (
      <div className="flex flex-col gap-3" data-testid="inpaint-canvas">
        <div className="overflow-hidden rounded-md border border-border" style={stageStyle}>
          <Stage
            width={naturalW}
            height={naturalH}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={endStroke}
            onPointerLeave={endStroke}
            onPointerCancel={endStroke}
            // Konva renders into a fixed pixel-sized canvas; CSS on the
            // wrapper above scales it to fit. `style={{width:'100%'}}` on
            // the inner canvas makes the visual size follow the wrapper.
            style={{ width: '100%', height: '100%', cursor: enabled ? 'crosshair' : 'default' }}
          >
            <Layer listening={false}>
              <KonvaImage image={image} width={naturalW} height={naturalH} />
            </Layer>
            <Layer ref={maskLayerRef} opacity={MASK_DISPLAY_OPACITY}>
              {strokes.map((stroke, idx) => (
                <Line
                  key={idx}
                  points={stroke.points}
                  stroke="#ffffff"
                  strokeWidth={stroke.size}
                  lineCap="round"
                  lineJoin="round"
                  tension={0.2}
                  globalCompositeOperation={
                    stroke.tool === 'eraser' ? 'destination-out' : 'source-over'
                  }
                />
              ))}
            </Layer>
          </Stage>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <div role="group" aria-label="筆刷工具" className="flex items-center gap-1">
            <Button
              type="button"
              variant={tool === 'brush' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setTool('brush')}
              disabled={!enabled}
              aria-pressed={tool === 'brush'}
              data-testid="inpaint-tool-brush"
            >
              <Paintbrush className="size-3.5" aria-hidden />
              筆刷
            </Button>
            <Button
              type="button"
              variant={tool === 'eraser' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setTool('eraser')}
              disabled={!enabled}
              aria-pressed={tool === 'eraser'}
              data-testid="inpaint-tool-eraser"
            >
              <Eraser className="size-3.5" aria-hidden />
              橡皮擦
            </Button>
          </div>

          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>筆刷大小</span>
            <input
              type="range"
              min={MIN_BRUSH}
              max={MAX_BRUSH}
              step={1}
              value={brushSize}
              onChange={(e) => setBrushSize(Number(e.target.value))}
              disabled={!enabled}
              aria-label="筆刷大小"
              data-testid="inpaint-brush-size"
            />
            <span className="tabular-nums">{brushSize}</span>
          </label>

          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleClear}
            disabled={!enabled || strokes.length === 0}
            data-testid="inpaint-clear"
          >
            <Trash2 className="size-3.5" aria-hidden />
            清除 mask
          </Button>
        </div>
      </div>
    )
  },
)
