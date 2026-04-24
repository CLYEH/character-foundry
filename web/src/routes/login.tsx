import { Link } from 'react-router'

export default function LoginPage() {
  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">登入</h1>
      <p className="text-sm text-muted-foreground">Login form arrives in T-008.</p>
      <Link to="/" className="text-sm underline underline-offset-4">
        回首頁
      </Link>
    </section>
  )
}
