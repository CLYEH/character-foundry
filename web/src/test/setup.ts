import '@testing-library/jest-dom/vitest'

// jsdom does not implement matchMedia, but Sonner / next-themes both call it
// when the Toaster mounts. Provide a minimal stub so rendering AppLayout in
// tests does not explode.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
