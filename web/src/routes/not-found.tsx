import { Link } from 'react-router'

export default function NotFoundPage() {
  return (
    <section className="flex flex-col items-start gap-3">
      <h1 className="text-2xl font-semibold">404</h1>
      <p className="text-sm text-muted-foreground">找不到這個頁面。</p>
      <Link to="/" className="text-sm underline underline-offset-4">
        回首頁
      </Link>
    </section>
  )
}
