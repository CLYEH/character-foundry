import { type ReactNode } from 'react'
import { Check } from 'lucide-react'

import { cn } from '@/lib/cn'

export interface InputModeCardProps {
  value: string
  label: string
  description: string
  icon: ReactNode
  selected: boolean
  onSelect: (value: string) => void
}

export function InputModeCard({
  value,
  label,
  description,
  icon,
  selected,
  onSelect,
}: InputModeCardProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-testid={`input-mode-card-${value}`}
      onClick={() => onSelect(value)}
      className={cn(
        'flex flex-col items-start gap-3 rounded-xl border bg-card p-6 text-left shadow-sm transition outline-none',
        'hover:border-primary/50 hover:shadow-md',
        'focus-visible:ring-2 focus-visible:ring-ring',
        selected ? 'border-primary ring-2 ring-primary' : 'border-border',
      )}
    >
      <div className="flex w-full items-center justify-between">
        <div aria-hidden>{icon}</div>
        {selected && (
          <span
            aria-hidden
            className="flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground"
          >
            <Check className="size-4" />
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold">{label}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </button>
  )
}
