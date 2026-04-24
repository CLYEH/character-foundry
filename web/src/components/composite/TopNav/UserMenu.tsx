import { ChevronDown, LogOut, Settings } from 'lucide-react'
import { Link } from 'react-router'

import { logout as logoutEndpoint } from '@/api/endpoints/auth'
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
import { useAuthStore } from '@/stores/authStore'

function displayInitial(name: string): string {
  const trimmed = name.trim()
  return trimmed ? trimmed[0].toUpperCase() : '?'
}

export function UserMenu() {
  const user = useAuthStore((s) => s.user)

  if (!user) return null

  const handleLogout = async () => {
    const refreshToken = useAuthStore.getState().refreshToken
    if (refreshToken) {
      try {
        await logoutEndpoint(refreshToken)
      } catch {
        // Best-effort server-side revoke; clear local state regardless.
      }
    }
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
