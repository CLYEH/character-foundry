import { ArrowLeft, ImageIcon, Loader2, ListChecks } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate } from 'react-router'
import { zodResolver } from '@hookform/resolvers/zod'

import { ApiError } from '@/api/client'
import { useCreateCharacter } from '@/api/mutations/useCreateCharacter'
import { InputModeCard } from '@/components/characters'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { newCharacterSchema, type NewCharacterInput } from '@/lib/validators'

const NAME_MAX = 50

export default function NewCharacterPage() {
  const navigate = useNavigate()
  const { mutateAsync, isPending } = useCreateCharacter()

  // `input_mode` is intentionally absent from defaults so the submit
  // button stays disabled until the user picks a card; the zod resolver
  // enforces the enum at submit time.
  const form = useForm<NewCharacterInput>({
    resolver: zodResolver(newCharacterSchema),
    defaultValues: { name: '' },
    mode: 'onSubmit',
  })

  const {
    register,
    handleSubmit,
    setValue,
    setError,
    watch,
    formState: { errors },
  } = form

  const name = watch('name')
  const inputMode = watch('input_mode')
  const trimmedLength = name.trim().length
  const submitDisabled =
    isPending || trimmedLength === 0 || trimmedLength > NAME_MAX || !inputMode

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-8">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/" aria-label="回 Dashboard">
            <ArrowLeft className="size-4" />
            回 Dashboard
          </Link>
        </Button>
      </div>

      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">新增角色</h1>
        <p className="text-sm text-muted-foreground">
          先為角色取個名字，再選擇建立方式。
        </p>
      </header>

      <form
        noValidate
        className="flex flex-col gap-8"
        onSubmit={handleSubmit(async (values) => {
          try {
            const result = await mutateAsync({
              name: values.name.trim(),
              input_mode: values.input_mode,
            })
            navigate(`/characters/new/session/${result.creation_session.id}`, {
              replace: true,
            })
          } catch (err) {
            // VALIDATION_* and CONFLICT_* both map to the inline UI layer
            // (see lib/agentError.ts → mapAgentErrorToUI), so the global
            // toast handler skips them. Pin them to the name field — the
            // ticket calls out CONFLICT_DUPLICATE_NAME explicitly, and
            // VALIDATION_INVALID_CHARS also lands here when the backend
            // regex rejects characters our client schema allows.
            if (
              err instanceof ApiError &&
              (err.code === 'CONFLICT_DUPLICATE_NAME' || err.code.startsWith('VALIDATION_'))
            ) {
              setError('name', {
                type: 'server',
                message:
                  err.code === 'CONFLICT_DUPLICATE_NAME'
                    ? '你已有一個同名角色'
                    : err.message || '名稱含有不允許的字元',
              })
              return
            }
            // Other failures (MODEL_, AUTH_, INTERNAL_, network) fall
            // through to the global mutationCache toast in queryClient.ts.
          }
        })}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="character-name">先為角色取個名字</Label>
          <Input
            id="character-name"
            type="text"
            autoComplete="off"
            maxLength={NAME_MAX}
            aria-invalid={errors.name ? 'true' : undefined}
            aria-describedby="character-name-hint"
            {...register('name')}
          />
          <div
            id="character-name-hint"
            className="flex items-center justify-between text-xs text-muted-foreground"
          >
            <span>最多 {NAME_MAX} 字（中文／英數／底線／連字號）</span>
            <span data-testid="name-counter">
              {trimmedLength}/{NAME_MAX}
            </span>
          </div>
          {errors.name && (
            <p role="alert" className="text-xs text-destructive">
              {errors.name.message}
            </p>
          )}
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-medium">選擇建立方式</legend>
          <div
            role="radiogroup"
            aria-label="建立方式"
            className="grid gap-4 md:grid-cols-2"
          >
            <InputModeCard
              value="template"
              label="選單式（Template）"
              description="從性別、髮型、風格等選單組裝，再用文字補述細節。"
              icon={<ListChecks className="size-8 text-primary" aria-hidden />}
              selected={inputMode === 'template'}
              onSelect={() =>
                setValue('input_mode', 'template', { shouldValidate: true, shouldDirty: true })
              }
            />
            <InputModeCard
              value="reference"
              label="參考圖式（Reference）"
              description="上傳參考圖，AI 依據它生成風格相似的角色。"
              icon={<ImageIcon className="size-8 text-primary" aria-hidden />}
              selected={inputMode === 'reference'}
              onSelect={() =>
                setValue('input_mode', 'reference', { shouldValidate: true, shouldDirty: true })
              }
            />
          </div>
          {errors.input_mode && (
            <p role="alert" className="text-xs text-destructive">
              {errors.input_mode.message}
            </p>
          )}
        </fieldset>

        <div className="flex justify-end">
          <Button type="submit" disabled={submitDisabled} size="lg">
            {isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {isPending ? '建立中…' : '建立'}
          </Button>
        </div>
      </form>
    </section>
  )
}
