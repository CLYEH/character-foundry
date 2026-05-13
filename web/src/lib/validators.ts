import { z } from 'zod'

// Length aligned with planning/data/db-schema.md §3.3 + T-016 backend
// `NameStr` (1–50). Charset enforcement is left to the backend's
// VALIDATION_INVALID_CHARS (surfaced inline) so the client schema stays
// the lighter contract the ticket spec asks for.
export const newCharacterSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: '請輸入角色名稱' })
    .max(50, { message: '名稱最多 50 字' }),
  input_mode: z.enum(['template', 'reference'], {
    errorMap: () => ({ message: '請選擇建立方式' }),
  }),
})

export type NewCharacterInput = z.infer<typeof newCharacterSchema>
