import { create } from 'zustand'

interface ToastState {
  placeholder: never[]
}

export const useToastStore = create<ToastState>()(() => ({
  placeholder: [],
}))
