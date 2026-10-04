// AAR tab: the shared after-action report, its comments and reactions.
import { AarPanel } from '../../components/AarPanel'

export function AarTab({ brId, canManage }: { brId: string; canManage: boolean }) {
  return (
    <section className="panel aar-tab" data-testid="aar-section">
      <AarPanel brId={brId} canManage={canManage} />
    </section>
  )
}
