import { ErrorPage } from './ErrorPage'

export interface NotFoundPageProps {
  title?: string
  description?: string
}

export function NotFoundPage({
  title = '找不到這個頁面',
  description = '它可能已被刪除或搬家。',
}: NotFoundPageProps) {
  return <ErrorPage icon="🎭" title={title} description={description} testId="not-found-page" />
}
