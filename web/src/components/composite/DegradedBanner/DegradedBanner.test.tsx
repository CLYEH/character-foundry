import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { DegradedBanner } from './DegradedBanner'
import type { MetaResponse } from '@/api/endpoints/meta'

function baseMeta(overrides: Partial<MetaResponse> = {}): MetaResponse {
  return {
    models: {},
    preset_motions: [],
    platform_constraints_version: 'v1',
    api_version: 'v1',
    degraded_services: [],
    ...overrides,
  }
}

function renderWithClient(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <DegradedBanner />
    </QueryClientProvider>,
  )
}

describe('DegradedBanner', () => {
  let client: QueryClient

  beforeEach(() => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchInterval: false } },
    })
  })

  afterEach(() => {
    client.clear()
  })

  it('renders nothing when degraded_services is empty', () => {
    client.setQueryData(['meta'], baseMeta())
    renderWithClient(client)
    expect(screen.queryByTestId('degraded-banner')).not.toBeInTheDocument()
  })

  it('renders nothing while meta is loading (no data yet)', () => {
    renderWithClient(client)
    expect(screen.queryByTestId('degraded-banner')).not.toBeInTheDocument()
  })

  it('renders a banner entry for each known degraded service', () => {
    client.setQueryData(
      ['meta'],
      baseMeta({
        degraded_services: [
          { service: 'gpt-image-2', reason: 'CIRCUIT_OPEN' },
          { service: 'reconciler' },
        ],
      }),
    )
    renderWithClient(client)
    const banner = screen.getByTestId('degraded-banner')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveTextContent(/圖像生成.*暫時降級/)
    expect(banner).toHaveTextContent(/Prompt 最佳化.*暫時降級/)
  })

  it('prefers the backend-provided message over the default template', () => {
    client.setQueryData(
      ['meta'],
      baseMeta({
        degraded_services: [
          {
            service: 'veo-3.1',
            message: '動作影片生成暫時降級，預計 5 分鐘後恢復。',
          },
        ],
      }),
    )
    renderWithClient(client)
    expect(screen.getByTestId('degraded-banner')).toHaveTextContent(
      '動作影片生成暫時降級，預計 5 分鐘後恢復。',
    )
  })

  it('falls back to the raw service name for unknown services', () => {
    client.setQueryData(['meta'], baseMeta({ degraded_services: [{ service: 'storage-backend' }] }))
    renderWithClient(client)
    expect(screen.getByTestId('degraded-banner')).toHaveTextContent(/storage-backend.*暫時降級/)
  })
})
