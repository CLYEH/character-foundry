import { apiFetch } from '@/api/client'

export interface CharacterOwner {
  id: string
  name: string
}

export interface Character {
  id: string
  name: string
  slug: string
  owner: CharacterOwner
  base_thumbnail_url: string | null
  alias_count: number
  motion_count: number
  created_at: string
  updated_at: string
}

export interface CharacterListResponse {
  items: Character[]
  next_cursor: string | null
}

export interface ListCharactersParams {
  owner_id?: string
  q?: string
  limit?: number
  cursor?: string | null
}

export function listCharacters(params: ListCharactersParams = {}): Promise<CharacterListResponse> {
  const qs = new URLSearchParams()
  if (params.owner_id) qs.set('owner_id', params.owner_id)
  if (params.q) qs.set('q', params.q)
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.cursor) qs.set('cursor', params.cursor)
  const suffix = qs.toString()
  return apiFetch<CharacterListResponse>(`/v1/characters${suffix ? `?${suffix}` : ''}`)
}

export type InputMode = 'template' | 'reference'

export interface CreateCharacterRequest {
  name: string
  input_mode: InputMode
}

export interface CreationSession {
  id: string
  character_id: string | null
  input_mode: InputMode
  status: 'in_progress' | 'completed' | 'abandoned'
  checkpoint_count: number
  created_at: string
  completed_at: string | null
}

export interface CreateCharacterResponse {
  character: Character
  creation_session: CreationSession
}

export function createCharacter(input: CreateCharacterRequest) {
  return apiFetch<CreateCharacterResponse>('/v1/characters', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
