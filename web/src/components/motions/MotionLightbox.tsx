import type { Motion } from '@/api/endpoints/motions'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface MotionLightboxProps {
  motion: Motion | null
  onClose: () => void
}

/**
 * Plays a motion video in a modal. The component is mounted alongside
 * `MotionRow`; rendering with `motion === null` keeps the `<Dialog>`
 * closed so we don't unmount/remount the underlying Radix portal on
 * every preset / custom click.
 */
export function MotionLightbox({ motion, onClose }: MotionLightboxProps) {
  const isOpen = motion !== null
  return (
    <Dialog open={isOpen} onOpenChange={(open) => (!open ? onClose() : null)}>
      <DialogContent data-testid="motion-lightbox" className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{motion?.name ?? ''}</DialogTitle>
          <DialogDescription className="sr-only">Motion 影片預覽</DialogDescription>
        </DialogHeader>
        {motion?.video_url ? (
          <video
            data-testid="motion-lightbox-video"
            src={motion.video_url}
            controls
            autoPlay
            playsInline
            className="w-full rounded bg-black"
          />
        ) : (
          <p className="text-sm text-muted-foreground">影片連結讀不到，請稍後再試。</p>
        )}
      </DialogContent>
    </Dialog>
  )
}
