import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Checkpoint } from '@/api/endpoints/checkpoints'

export interface CheckpointLightboxProps {
  checkpoint: Checkpoint | null
  onClose: () => void
}

/**
 * Minimum-viable lightbox per the ticket: full-screen image + prompt_summary.
 * Detailed prompt breakdown lives behind T-024's Advanced Prompt modal.
 */
export function CheckpointLightbox({ checkpoint, onClose }: CheckpointLightboxProps) {
  const open = checkpoint !== null

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent data-testid="checkpoint-lightbox" className="max-h-[90vh] sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Checkpoint #{checkpoint?.sequence ?? '-'}</DialogTitle>
          <DialogDescription>{checkpoint?.prompt_summary ?? '尚無摘要'}</DialogDescription>
        </DialogHeader>
        {checkpoint?.output_image_url ? (
          <img
            src={checkpoint.output_image_url}
            alt={`Checkpoint ${checkpoint.sequence}`}
            className="mx-auto max-h-[60vh] w-auto rounded"
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
