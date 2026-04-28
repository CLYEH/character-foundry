import { useCallback, useRef, useState, type DragEvent } from 'react'
import { Upload } from 'lucide-react'

import { ALLOWED_REFERENCE_MIME_TYPES, MAX_REFERENCE_IMAGES } from '@/hooks/useReferenceUpload'

export interface ReferenceImageDropzoneProps {
  onFiles: (files: File[]) => void
  remaining: number
  disabled?: boolean
}

const ACCEPT_ATTR = ALLOWED_REFERENCE_MIME_TYPES.join(',')

/**
 * Drop / click target for reference images. Pure UI — validation and
 * upload state live in `useReferenceUpload`. Native drag events keep
 * us off `react-dropzone`; the surface is small enough that the
 * dependency wasn't worth the bundle cost.
 */
export function ReferenceImageDropzone({
  onFiles,
  remaining,
  disabled,
}: ReferenceImageDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [hover, setHover] = useState(false)
  const isFull = remaining <= 0
  const inputDisabled = Boolean(disabled) || isFull

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setHover(false)
      if (inputDisabled) return
      const files = Array.from(e.dataTransfer.files ?? [])
      if (files.length === 0) return
      onFiles(files)
    },
    [inputDisabled, onFiles],
  )

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      if (inputDisabled) return
      setHover(true)
    },
    [inputDisabled],
  )

  const handleDragLeave = useCallback(() => setHover(false), [])

  const handleClick = useCallback(() => {
    if (inputDisabled) return
    inputRef.current?.click()
  }, [inputDisabled])

  return (
    <div
      data-testid="reference-image-dropzone"
      data-active={hover ? 'true' : undefined}
      role="button"
      tabIndex={inputDisabled ? -1 : 0}
      aria-disabled={inputDisabled || undefined}
      aria-label="上傳參考圖"
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          handleClick()
        }
      }}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      className={[
        'flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-6 text-center text-sm transition-colors',
        inputDisabled
          ? 'cursor-not-allowed border-border/40 bg-muted/30 text-muted-foreground'
          : hover
            ? 'cursor-pointer border-primary/60 bg-primary/5 text-primary'
            : 'cursor-pointer border-border/60 bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground',
      ].join(' ')}
    >
      <Upload className="size-5" aria-hidden />
      <div className="font-medium text-foreground">
        {isFull ? `已達上限（${MAX_REFERENCE_IMAGES} 張）` : '拖放或點擊上傳參考圖'}
      </div>
      <div className="text-xs">PNG / JPEG / WebP，單檔 ≤ 10 MB，最多 {MAX_REFERENCE_IMAGES} 張</div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        multiple
        disabled={inputDisabled}
        className="sr-only"
        data-testid="reference-image-input"
        onClick={(e) => {
          // The input is a child of the wrapper div, so a programmatic
          // `inputRef.current?.click()` from `handleClick` would
          // dispatch a click event on the input that bubbles back up
          // to the wrapper, re-entering `handleClick` recursively
          // (Codex P1 round 5 on PR #31). Stop the click here so the
          // wrapper's onClick only fires for the user's original
          // click on the dropzone.
          e.stopPropagation()
        }}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          if (files.length > 0) onFiles(files)
          // Reset so picking the same file twice still fires onChange.
          e.target.value = ''
        }}
      />
    </div>
  )
}
