import { CheckpointCard, type CheckpointCardProps } from './CheckpointCard'
import type { CheckpointCardModel } from './types'

export interface CheckpointListProps extends Omit<CheckpointCardProps, 'model'> {
  models: CheckpointCardModel[]
}

export function CheckpointList({ models, ...handlers }: CheckpointListProps) {
  if (models.length === 0) {
    return (
      <div
        data-testid="checkpoint-list-empty"
        className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border/60 bg-muted/30 p-8 text-sm text-muted-foreground"
      >
        <p>還沒有 Checkpoint</p>
        <p className="text-xs">設定輸入條件，按生成開始</p>
      </div>
    )
  }

  return (
    <ul
      data-testid="checkpoint-list"
      className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-3"
    >
      {models.map((model) => (
        <li key={model.checkpointId}>
          <CheckpointCard model={model} {...handlers} />
        </li>
      ))}
    </ul>
  )
}
