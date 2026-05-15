import { ChevronDown, LogOut, Settings } from 'lucide-react'
import { Link } from 'react-router'

import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { signOutServer, useAuthStore } from '@/stores/authStore'

function displayInitial(name: string): string {
  const trimmed = name.trim()
  return trimmed ? trimmed[0].toUpperCase() : '?'
}

export function UserMenu() {
  const user = useAuthStore((s) => s.user)

  if (!user) return null

  // T-078 deliberately keeps logout SPA-local — revoke the refresh
  // token, clear the store, and let `ProtectedRoute` bounce to /login
  // on the next render. The lingering `authentik_session` cookie is no
  // longer a re-login blocker now that the Google source's
  // authentication flow (`default-source-authentication`) has had its
  // `require_unauthenticated` policy relaxed; see
  // `planning/devops/authentik-stack.md` §5.2 + the cf-e2e-bootstrap
  // blueprint entry that codifies the relaxation. A previous T-078
  // iteration tried to chain logout through Authentik's OIDC
  // `end_session_endpoint` to also kill the Authentik session, but the
  // flow-interface hung intermittently at `ak-loading` and the dev
  // origin flipped from `:5173` to `:80` after the redirect, both
  // breaking the UX promise of "logout always lands you back on the
  // SPA's /login".
  const handleLogout = async () => {
    await signOutServer()
    useAuthStore.getState().logout()
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2 px-2" aria-label="使用者選單">
          <Avatar size="sm">
            <AvatarFallback>{displayInitial(user.name)}</AvatarFallback>
          </Avatar>
          <span className="hidden max-w-[12ch] truncate sm:inline-block">{user.name}</span>
          <ChevronDown className="size-4 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">{user.name}</span>
            <span className="truncate text-xs text-muted-foreground">{user.email}</span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/settings">
            <Settings />
            設定
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={handleLogout}>
          <LogOut />
          登出
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
