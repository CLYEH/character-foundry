import { useCallback, useEffect, useRef, useState } from 'react'

import { uploadReferenceImage } from '@/api/endpoints/reference-images'
import { AgentError } from '@/lib/agentError'
import { toast } from '@/stores/toastStore'

export const MAX_REFERENCE_IMAGES = 3
export const MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
export const ALLOWED_REFERENCE_MIME_TYPES: ReadonlyArray<string> = [
  'image/png',
  'image/jpeg',
  'image/webp',
]

export type ReferenceItemStatus = 'uploading' | 'ready' | 'error'

export interface ReferenceImageItem {
  localId: string
  fileName: string
  previewUrl: string
  status: ReferenceItemStatus
  /** Populated once the backend acknowledges the upload. */
  referenceImageId: string | null
  errorMessage: string | null
}

interface PendingUpload {
  localId: string
  file: File
}

/**
 * Owns the multi-file upload state for the reference-mode panel. Keeps
 * three concerns wired together:
 *
 *  1. Synchronous validation (MIME, size, magic-byte sniff, count cap)
 *     so a bad drop never reaches the network and the user sees a toast
 *     immediately.
 *  2. Per-file upload mutations — each file rides its own request so a
 *     single failure doesn't poison the rest of the batch.
 *  3. Object-URL bookkeeping so the preview stays painted while the
 *     upload is in flight, then swaps to the server-minted signed URL
 *     when the response lands. URLs are revoked on success / removal /
 *     unmount to avoid leaking blob references in long sessions.
 */
export function useReferenceUpload(sessionId: string) {
  const [items, setItems] = useState<ReferenceImageItem[]>([])

  // Track which localIds own which object URLs so we can revoke exactly
  // once. Storing inside the item works for happy-path cleanup but the
  // ref makes unmount cleanup deterministic even if the component
  // re-renders mid-revocation. Doubles as the synchronous source of
  // truth for capacity checks — `items.length` from React state is
  // captured by closure and goes stale if `addFiles` fires twice in
  // the same tick before a render flush.
  const objectUrlsRef = useRef<Map<string, string>>(new Map())
  // Hold the original File alongside its localId so retry can replay
  // the upload without forcing the user to re-pick the file.
  const filesRef = useRef<Map<string, File>>(new Map())
  // Suppress state updates from in-flight uploads after unmount; the
  // backend `reference_image_id` is still minted, but the client side
  // would otherwise warn and the orphan row is collected by the 7-day
  // cleanup job.
  const isMountedRef = useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    const urls = objectUrlsRef.current
    return () => {
      isMountedRef.current = false
      for (const url of urls.values()) URL.revokeObjectURL(url)
      urls.clear()
    }
  }, [])

  const revokeObjectUrl = useCallback((localId: string) => {
    const url = objectUrlsRef.current.get(localId)
    if (url) {
      URL.revokeObjectURL(url)
      objectUrlsRef.current.delete(localId)
    }
  }, [])

  const startUpload = useCallback(
    ({ localId, file }: PendingUpload) => {
      // Each file rides its own promise so a slow / failing upload
      // never blocks the rest of the batch — we deliberately bypass
      // `useMutation` here because TanStack tracks one mutation state
      // at a time and we need N parallel observations with N
      // independent state slots.
      void uploadReferenceImage(sessionId, file)
        .then((response) => {
          if (!isMountedRef.current) return
          revokeObjectUrl(localId)
          setItems((prev) =>
            prev.map((item) =>
              item.localId === localId
                ? {
                    ...item,
                    status: 'ready',
                    previewUrl: response.url,
                    referenceImageId: response.reference_image_id,
                    errorMessage: null,
                  }
                : item,
            ),
          )
        })
        .catch((err: unknown) => {
          if (!isMountedRef.current) return
          const agent = AgentError.from(err)
          setItems((prev) =>
            prev.map((item) =>
              item.localId === localId
                ? { ...item, status: 'error', errorMessage: agent.message }
                : item,
            ),
          )
        })
    },
    [revokeObjectUrl, sessionId],
  )

  const addFiles = useCallback(
    async (incoming: readonly File[]): Promise<void> => {
      if (incoming.length === 0) return

      // Capacity check reads `filesRef`, not React state — `items.length`
      // from a closure captured at render goes stale if the user fires
      // two `addFiles` calls in the same tick (drag-drop A then click-
      // pick B before the first render flushes), letting both batches
      // pass the cap and pile up >3 items. We use `filesRef` (and not
      // `objectUrlsRef`) because the object-URL map drains as uploads
      // settle; `filesRef` mirrors every active slot for the lifetime
      // of the entry, only shrinking on `remove` / `reset`.
      const accepted: PendingUpload[] = []
      let rejectedTooMany = 0
      const initiallyFull = filesRef.current.size >= MAX_REFERENCE_IMAGES

      for (const file of incoming) {
        if (filesRef.current.size >= MAX_REFERENCE_IMAGES) {
          rejectedTooMany += 1
          continue
        }
        const reason = await validateFile(file)
        if (reason) {
          toast.error(`${file.name}：${reason}`)
          continue
        }
        const localId = generateLocalId()
        const previewUrl = URL.createObjectURL(file)
        objectUrlsRef.current.set(localId, previewUrl)
        filesRef.current.set(localId, file)
        accepted.push({ localId, file })
        setItems((prev) => [
          ...prev,
          {
            localId,
            fileName: file.name,
            previewUrl,
            status: 'uploading',
            referenceImageId: null,
            errorMessage: null,
          },
        ])
      }

      if (rejectedTooMany > 0 || (initiallyFull && incoming.length > 0)) {
        toast.error(`最多 ${MAX_REFERENCE_IMAGES} 張參考圖`)
      }

      for (const pending of accepted) startUpload(pending)
    },
    [startUpload],
  )

  const remove = useCallback(
    (localId: string) => {
      revokeObjectUrl(localId)
      filesRef.current.delete(localId)
      setItems((prev) => prev.filter((item) => item.localId !== localId))
    },
    [revokeObjectUrl],
  )

  const retry = useCallback(
    (localId: string) => {
      const file = filesRef.current.get(localId)
      if (!file) return
      // The retry button is only rendered for `status === 'error'`,
      // so we trust the caller's status here and just flip the flag.
      setItems((prev) =>
        prev.map((i) =>
          i.localId === localId && i.status === 'error'
            ? { ...i, status: 'uploading', errorMessage: null }
            : i,
        ),
      )
      startUpload({ localId, file })
    },
    [startUpload],
  )

  const reset = useCallback(() => {
    for (const url of objectUrlsRef.current.values()) URL.revokeObjectURL(url)
    objectUrlsRef.current.clear()
    filesRef.current.clear()
    setItems([])
  }, [])

  const referenceImageIds = items
    .filter((item) => item.status === 'ready' && item.referenceImageId)
    .map((item) => item.referenceImageId as string)

  const isUploading = items.some((item) => item.status === 'uploading')

  return {
    items,
    referenceImageIds,
    isUploading,
    addFiles,
    remove,
    retry,
    reset,
  }
}

async function validateFile(file: File): Promise<string | null> {
  if (file.size > MAX_REFERENCE_IMAGE_BYTES) {
    return '檔案過大（上限 10 MB）'
  }
  // `File.type` is set from the OS-reported MIME and is trivially
  // spoofable via extension renames. Sniff the first bytes so we don't
  // hand the server a JPEG masquerading as PNG just to be rejected on
  // round-trip.
  const sniffed = await sniffImageType(file)
  if (!sniffed) {
    return '檔案格式不支援（PNG / JPEG / WebP）'
  }
  if (!ALLOWED_REFERENCE_MIME_TYPES.includes(sniffed)) {
    return '檔案格式不支援（PNG / JPEG / WebP）'
  }
  return null
}

async function sniffImageType(file: File): Promise<string | null> {
  // 12 bytes covers PNG (8-byte signature), JPEG (3-byte SOI), and
  // WebP (RIFF....WEBP — the 'WEBP' tag lands at offset 8). Size has
  // already been validated <= 10 MB by the caller, so reading the full
  // buffer is bounded; we go through `readBlobBytes` because jsdom
  // (used in tests) and a handful of older browsers don't implement
  // `Blob.arrayBuffer()` directly.
  const head = await readBlobBytes(file, 12)
  if (head.length < 3) return null

  if (
    head[0] === 0x89 &&
    head[1] === 0x50 &&
    head[2] === 0x4e &&
    head[3] === 0x47 &&
    head[4] === 0x0d &&
    head[5] === 0x0a &&
    head[6] === 0x1a &&
    head[7] === 0x0a
  ) {
    return 'image/png'
  }

  if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) {
    return 'image/jpeg'
  }

  if (
    head.length >= 12 &&
    head[0] === 0x52 &&
    head[1] === 0x49 &&
    head[2] === 0x46 &&
    head[3] === 0x46 &&
    head[8] === 0x57 &&
    head[9] === 0x45 &&
    head[10] === 0x42 &&
    head[11] === 0x50
  ) {
    return 'image/webp'
  }

  return null
}

async function readBlobBytes(blob: Blob, byteCount: number): Promise<Uint8Array> {
  const slice = blob.slice(0, byteCount)
  const sliceWithBuffer = slice as Blob & { arrayBuffer?: () => Promise<ArrayBuffer> }
  if (typeof sliceWithBuffer.arrayBuffer === 'function') {
    return new Uint8Array(await sliceWithBuffer.arrayBuffer())
  }
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (result instanceof ArrayBuffer) resolve(new Uint8Array(result))
      else reject(new Error('FileReader did not return ArrayBuffer'))
    }
    reader.onerror = () => reject(reader.error ?? new Error('FileReader error'))
    reader.readAsArrayBuffer(slice)
  })
}

function generateLocalId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `ref-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}
