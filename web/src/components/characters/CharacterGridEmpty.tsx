import { Link } from 'react-router'

import { Button } from '@/components/ui/button'

export function CharacterGridEmpty() {
  return (
    <div
      data-testid="dashboard-empty"
      className="flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center"
    >
      <div aria-hidden className="text-6xl">
        🎭
      </div>
      <p className="text-sm text-muted-foreground">還沒有角色，建一個吧</p>
      <Button asChild size="lg">
        <Link to="/characters/new">建立 Character</Link>
      </Button>
    </div>
  )
}
