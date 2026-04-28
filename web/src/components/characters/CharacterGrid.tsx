import type { Character } from '@/api/endpoints/characters'

import { CharacterCard } from './CharacterCard'

export interface CharacterGridProps {
  characters: Character[]
  currentUserId: string | null
}

export function CharacterGrid({ characters, currentUserId }: CharacterGridProps) {
  return (
    <div
      data-testid="character-grid"
      className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4"
    >
      {characters.map((character) => (
        <CharacterCard
          key={character.id}
          character={character}
          isOwner={currentUserId !== null && character.owner.id === currentUserId}
        />
      ))}
    </div>
  )
}
