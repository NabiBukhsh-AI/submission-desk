import { CheckCircle2, CircleDashed, Mail, OctagonAlert, Send, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react'
import type { Chip } from '@/lib/api'
import { cn } from '@/lib/utils'

// A status is never carried by colour alone: icon, colour, and the sentence
// the API supplies. The icon is chosen by status; the words are not ours.
const ICONS: Record<string, { icon: typeof CheckCircle2; tone: string }> = {
  ready_for_review: { icon: CheckCircle2, tone: 'text-success' },
  needs_review: { icon: TriangleAlert, tone: 'text-warning' },
  quarantined: { icon: OctagonAlert, tone: 'text-destructive' },
  manual_review_required: { icon: TriangleAlert, tone: 'text-warning' },
  interrupted: { icon: TriangleAlert, tone: 'text-warning' },
  needs_info: { icon: Mail, tone: 'text-muted-foreground' },
  approved: { icon: ThumbsUp, tone: 'text-success' },
  rejected: { icon: ThumbsDown, tone: 'text-muted-foreground' },
  delivered: { icon: Send, tone: 'text-success' },
  delivery_pending_retry: { icon: Send, tone: 'text-warning' },
  failed_terminal: { icon: OctagonAlert, tone: 'text-destructive' },
}

export function StatusChip({ status, chip, tier }: { status: string; chip: Chip; tier?: string }) {
  const { icon: Icon, tone } = ICONS[status] ?? { icon: CircleDashed, tone: 'text-muted-foreground' }
  const spinning = !ICONS[status]
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <Icon className={cn('size-4 shrink-0', tone, spinning && 'animate-spin')} aria-hidden="true" />
      <span>{chip.label}</span>
      {tier === 'suspect' && (
        <span className="rounded border border-warning/50 px-1 text-xs text-warning">flagged</span>
      )}
    </span>
  )
}
