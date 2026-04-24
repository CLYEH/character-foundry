import { Link } from 'react-router'

import { SearchInput } from './SearchInput'
import { UsageWidget } from './UsageWidget'
import { UserMenu } from './UserMenu'

export function TopNav() {
  return (
    <header className="sticky top-0 z-40 flex h-14 items-center gap-4 border-b border-border bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60 md:gap-6 md:px-6">
      <Link
        to="/"
        className="flex shrink-0 items-center gap-2 text-sm font-semibold"
        aria-label="回首頁"
      >
        <span aria-hidden>🎭</span>
        <span className="hidden sm:inline">Character Foundry</span>
      </Link>
      <div className="flex-1">
        <SearchInput />
      </div>
      <UsageWidget />
      <UserMenu />
    </header>
  )
}
