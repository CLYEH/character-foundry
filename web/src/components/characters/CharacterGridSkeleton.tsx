import { Skeleton } from '@/components/ui/skeleton'

const SKELETON_COUNT = 6

export function CharacterGridSkeleton() {
  return (
    <div
      data-testid="dashboard-skeleton"
      className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4"
    >
      {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-xl border bg-card shadow-sm">
          <Skeleton className="aspect-[3/4] w-full rounded-none" />
          <div className="flex flex-col gap-2 px-4 py-3">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  )
}
