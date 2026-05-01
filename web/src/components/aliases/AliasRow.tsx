import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'

import type { Alias } from '@/api/endpoints/aliases'
import { useAliasMotions } from '@/api/queries/useAliasMotions'
import { useDeleteAlias } from '@/api/mutations/useDeleteAlias'
import { useRenameAlias } from '@/api/mutations/useRenameAlias'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { AgentError } from '@/lib/agentError'
import { MotionRow } from '@/components/motions'
import { AliasDeleteConfirm } from './AliasDeleteConfirm'
import { AliasRenameInline } from './AliasRenameInline'

export interface AliasRowProps {
  alias: Alias
  characterId: string
  isOwner: boolean
}

/**
 * P-05 alias card. Renders the alias image + name + MotionRow plus
 * owner-only [編輯名稱] [刪除] affordances. Non-owner viewers see the
 * same data but the action buttons are disabled with a tooltip.
 */
export function AliasRow({ alias, characterId, isOwner }: AliasRowProps) {
  const motionsQuery = useAliasMotions(alias.id)
  const renameMutation = useRenameAlias(characterId)
  const deleteMutation = useDeleteAlias(characterId)

  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const renameError = renameMutation.error ? AgentError.from(renameMutation.error).message : null
  const deleteError = deleteMutation.error ? AgentError.from(deleteMutation.error).message : null

  const motions = motionsQuery.data?.items ?? []
  const src = alias.image_url ?? alias.thumbnail_url

  const startRename = () => {
    if (!isOwner) return
    renameMutation.reset()
    setRenameOpen(true)
  }
  const startDelete = () => {
    if (!isOwner) return
    deleteMutation.reset()
    setDeleteOpen(true)
  }

  return (
    <Card data-testid={`alias-row-${alias.id}`} className="overflow-hidden p-0">
      <div className="grid grid-cols-1 gap-0 md:grid-cols-[16rem_1fr]">
        <div className="aspect-square w-full bg-muted md:aspect-auto md:h-full">
          {src ? (
            <img
              src={src}
              alt={alias.name}
              loading="lazy"
              className="h-full w-full object-cover"
              data-testid={`alias-row-image-${alias.id}`}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">
              無預覽
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3 p-4">
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div className="flex flex-col gap-1">
              {renameOpen ? (
                <AliasRenameInline
                  aliasId={alias.id}
                  initialName={alias.name}
                  isPending={renameMutation.isPending}
                  errorMessage={renameError}
                  onCancel={() => setRenameOpen(false)}
                  onSubmit={(newName) =>
                    renameMutation.mutate(
                      { aliasId: alias.id, body: { name: newName } },
                      { onSuccess: () => setRenameOpen(false) },
                    )
                  }
                />
              ) : (
                <h3 data-testid={`alias-row-name-${alias.id}`} className="text-lg font-semibold">
                  {alias.name}
                </h3>
              )}
              <p className="text-xs text-muted-foreground">
                建立於{' '}
                <time dateTime={alias.created_at}>
                  {new Date(alias.created_at).toLocaleDateString()}
                </time>
              </p>
            </div>
            {!renameOpen ? (
              <div className="flex items-center gap-2">
                <OwnerActionButton
                  isOwner={isOwner}
                  testId={`alias-row-rename-${alias.id}`}
                  label="編輯名稱"
                  icon={<Pencil className="size-3.5" aria-hidden />}
                  onClick={startRename}
                />
                <OwnerActionButton
                  isOwner={isOwner}
                  testId={`alias-row-delete-${alias.id}`}
                  label="刪除"
                  variant="outline"
                  icon={<Trash2 className="size-3.5" aria-hidden />}
                  onClick={startDelete}
                />
              </div>
            ) : null}
          </div>
          <MotionRow
            parentType="alias"
            parentId={alias.id}
            motions={motions}
            isOwner={isOwner}
            isLoading={motionsQuery.isPending}
          />
        </div>
      </div>
      <AliasDeleteConfirm
        isOpen={deleteOpen}
        aliasName={alias.name}
        isPending={deleteMutation.isPending}
        errorMessage={deleteError}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() =>
          deleteMutation.mutate({ aliasId: alias.id }, { onSuccess: () => setDeleteOpen(false) })
        }
      />
    </Card>
  )
}

interface OwnerActionButtonProps {
  isOwner: boolean
  label: string
  testId: string
  icon: React.ReactNode
  variant?: 'default' | 'outline'
  onClick: () => void
}

function OwnerActionButton({
  isOwner,
  label,
  testId,
  icon,
  variant = 'outline',
  onClick,
}: OwnerActionButtonProps) {
  const button = (
    <Button
      type="button"
      variant={variant}
      size="sm"
      disabled={!isOwner}
      onClick={isOwner ? onClick : undefined}
      data-testid={testId}
    >
      {icon}
      {label}
    </Button>
  )
  if (isOwner) return button
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{button}</span>
      </TooltipTrigger>
      <TooltipContent>僅 owner 可操作</TooltipContent>
    </Tooltip>
  )
}
