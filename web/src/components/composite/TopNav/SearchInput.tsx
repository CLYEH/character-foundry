import { Search } from 'lucide-react'

import { Input } from '@/components/ui/input'

export function SearchInput() {
  return (
    <form
      role="search"
      className="relative w-full max-w-sm"
      onSubmit={(e) => {
        e.preventDefault()
      }}
    >
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        placeholder="搜尋角色..."
        aria-label="搜尋角色"
        disabled
        className="pl-8"
      />
    </form>
  )
}
