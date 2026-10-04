// A pilot's name followed by their corporation and alliance tickers.
import { affiliationTitle, tickerText, useEntities } from '../entities'

interface Props {
  name: string
  /** Preferred lookup key; the name is used when it is absent or unknown. */
  characterId?: number | null
  className?: string
}

export function PilotName({ name, characterId, className }: Props) {
  const entities = useEntities()
  const tags = entities.byId(characterId) ?? entities.byName(name)
  const tickers = tickerText(tags)
  return (
    <span className={className ? `pilot-name ${className}` : 'pilot-name'} title={affiliationTitle(tags)}>
      {name}
      {tickers && <span className="pilot-tags">{tickers}</span>}
    </span>
  )
}
