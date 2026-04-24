import { z } from 'zod'

export const loginSchema = z.object({
  email: z.string().min(1, { message: '請輸入 Email' }).email({ message: '請輸入有效的 Email' }),
  password: z.string().min(1, { message: '請輸入密碼' }),
})

export type LoginInput = z.infer<typeof loginSchema>
