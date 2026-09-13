import {
  ArrowRight,
  BadgeDollarSign,
  Ban,
  BookOpenCheck,
  Coins,
  EyeOff,
  FileSearch,
  FileText,
  Fingerprint,
  Gavel,
  KeyRound,
  Layers,
  ListChecks,
  LockKeyhole,
  Quote,
  Scale,
  ScanSearch,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { href } from '@/lib/router'

// The public page. Everything it claims is a mechanism in the codebase, and
// each card names the file or test that holds the line, so a reader can check.
// Nothing here is a metric of the model's quality; those live in EVALUATION.md
// with their confidence intervals.

const STEPS = [
  { icon: FileText, title: 'Intake', text: 'Documents are typed by their bytes, size-capped, and stored by content hash — never by the name they arrived with.' },
  { icon: ScanSearch, title: 'Extract & sanitise', text: 'Text with page and offset maps. Ten deterministic detectors look for instructions hidden in the document before any model reads it.' },
  { icon: Quote, title: 'Assess with quotations', text: 'For each requirement the model may only return quotations from the document, or say the document does not address it.' },
  { icon: ListChecks, title: 'Verify every quotation', text: 'Each quotation is located in the source text. One that is not there is rejected, kept for the reviewer, and counted.' },
  { icon: Gavel, title: 'A person decides', text: 'Two pure rules turn verified evidence into a band with a printed derivation. Nothing leaves without a reviewer’s decision.' },
]

const FEATURES = [
  { icon: Quote, title: 'Evidence first, never score first', text: 'The model’s schema has no field for a score, a rating, or a recommendation. It can only quote. A test fails the build if that ever changes.', where: 'domain/contracts/responses.py' },
  { icon: ListChecks, title: 'Span validation', text: 'Three valid tiers (exact, normalised, fuzzy for scanned pages) and three invalid ones. Rejected quotations are shown in their own panel rather than dropped.', where: 'domain/provenance/validator.py' },
  { icon: Layers, title: 'An eleven-node pipeline', text: 'CONFIG to DELIVER as plain functions over persisted state. Every node commits before the next starts, so a crash resumes where it stopped.', where: 'pipeline/' },
  { icon: Scale, title: 'A rule engine you can read', text: 'Coverage gate, weighted score, blocker dominance — two pure functions with a printed derivation and property tests for monotonicity.', where: 'domain/rules/' },
  { icon: BookOpenCheck, title: 'Rubrics as configuration', text: 'A role is a YAML file: requirements, weights, cutoffs, examples. Edit it on the roles page; every run records the hash of the rubric it used.', where: 'rubrics/' },
  { icon: UserCheck, title: 'The reviewer stays in charge', text: 'Overrides recompute through the same rules, with a reason code. The interface holds no business logic — an architecture test walks it.', where: 'application/use_cases/submit_review.py' },
  { icon: Coins, title: 'Cost from the first call', text: 'Usage is part of the model client’s return type. Prices are configuration; until they are entered every cost reads “not configured”, never zero.', where: 'application/accounting.py' },
  { icon: Sparkles, title: 'Runs with no key at all', text: 'A deterministic stand-in answers when no provider is configured, so the whole workflow — and 2,900 offline tests — run on a fresh clone.', where: 'infrastructure/models/stand_in.py' },
]

const PROTECTIONS = [
  { icon: ShieldAlert, title: 'Instructions hidden in a document', text: 'Ten detectors, a nonce-fenced document region the text cannot close, a schema with nowhere to comply, and quarantine before any model call — at zero spend.' },
  { icon: EyeOff, title: 'Exposure of candidate data', text: 'Blind mode removes identity before the model reads. Logs are redacted before any sink. Evidence spans are never logged by default.' },
  { icon: Ban, title: 'Delivery without a decision', text: 'The state machine allows delivery only from an approved run, and approval only from a persisted reviewer decision. Optimistic concurrency stops two reviewers colliding.' },
  { icon: FileSearch, title: 'Hostile files and path traversal', text: 'Bytes are typed by sniffing, not extension; sizes and page counts are capped; nothing from a filename decides where anything is written.' },
  { icon: BadgeDollarSign, title: 'Runaway spend', text: 'A per-run token ceiling sits in front of every model call. One repair, one escalation, a bounded retry: no loop can spend without a ledger row.' },
  { icon: KeyRound, title: 'Credentials', text: 'Keys saved on the admin page are sealed at rest and never returned to a browser. The doctor reports presence, never a value.' },
  { icon: Fingerprint, title: 'Unfair treatment', text: 'Protected attributes have no field in any schema. A counterfactual fairness experiment reports its flip rate with a noise floor — or refuses to.' },
  { icon: LockKeyhole, title: 'A stranger at the interface', text: 'One admin account: scrypt-hashed password, signed session cookie, every route past health guarded, CORS by named origin.' },
]

export function Home({ user }: { user: string | null }) {
  const primary = user ? href({ page: 'queue' }) : href({ page: 'login' })
  return (
    <div className="space-y-24 pb-16">
      <section className="mx-auto max-w-3xl pt-10 text-center sm:pt-20">
        <p className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
          <ShieldCheck className="size-3.5 text-primary" aria-hidden="true" />
          Evidence-first candidate screening
        </p>
        <h1 className="mt-6 text-4xl font-semibold tracking-tight sm:text-5xl">
          Screening that shows its evidence.
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
          Submission Desk reads a candidate’s documents against a role, quotes the document for every point it
          makes, verifies every quotation against the source, and puts a person — not a model — in charge of the
          decision.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button asChild size="lg">
            <a href={primary}>
              {user ? 'Open the queue' : 'Sign in'}
              <ArrowRight aria-hidden="true" />
            </a>
          </Button>
          <Button asChild size="lg" variant="outline">
            <a href="#how-it-works">How it works</a>
          </Button>
        </div>
        <dl className="mx-auto mt-12 grid max-w-2xl grid-cols-2 gap-6 sm:grid-cols-4">
          {[
            ['11', 'pipeline nodes'],
            ['0', 'scores a model can emit'],
            ['100%', 'of quotations verified'],
            ['2,900+', 'offline tests'],
          ].map(([value, label]) => (
            <div key={label}>
              <dt className="order-last text-xs text-muted-foreground">{label}</dt>
              <dd className="font-heading text-2xl font-semibold text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="how-it-works" aria-labelledby="how-heading" className="scroll-mt-24">
        <h2 id="how-heading" className="text-2xl font-semibold">
          How it works
        </h2>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Five stages a recruiter can explain to a candidate. Each one commits before the next starts.
        </p>
        <ol className="mt-8 grid gap-4 md:grid-cols-5">
          {STEPS.map(({ icon: Icon, title, text }, index) => (
            <li key={title} className="relative rounded-xl border bg-card p-5">
              <span className="absolute top-4 right-4 font-heading text-xs text-muted-foreground">{index + 1}</span>
              <Icon className="size-5 text-primary" aria-hidden="true" />
              <h3 className="mt-3 font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="features-heading">
        <h2 id="features-heading" className="text-2xl font-semibold">
          What is in the box
        </h2>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Each card names the file that holds the line, so any claim here can be checked against the code.
        </p>
        <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map(({ icon: Icon, title, text, where }) => (
            <li key={title} className="group rounded-xl border bg-card p-5 transition-colors hover:border-primary/40">
              <span className="inline-flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <h3 className="mt-4 font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{text}</p>
              <code className="mt-3 block truncate text-xs text-muted-foreground/80">{where}</code>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="protections-heading">
        <div className="rounded-2xl border bg-card p-6 sm:p-10">
          <div className="flex items-start gap-4">
            <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <ShieldCheck className="size-5" aria-hidden="true" />
            </span>
            <div>
              <h2 id="protections-heading" className="text-2xl font-semibold">
                What it is protected against
              </h2>
              <p className="mt-2 max-w-2xl text-muted-foreground">
                Eight threats, each with a mechanism and a test behind it. The full model, including what is{' '}
                <em>not</em> defended against, is in <code className="text-sm">docs/THREAT_MODEL.md</code>.
              </p>
            </div>
          </div>
          <ul className="mt-8 grid gap-x-8 gap-y-6 sm:grid-cols-2">
            {PROTECTIONS.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <Icon className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
                <div>
                  <h3 className="font-semibold">{title}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="text-center">
        <h2 className="text-2xl font-semibold">Ready to look at a candidate?</h2>
        <p className="mt-2 text-muted-foreground">
          Upload documents, pick a role, and read the evidence — every quotation verified before you see it.
        </p>
        <Button asChild size="lg" className="mt-6">
          <a href={primary}>
            {user ? 'Open the queue' : 'Sign in'}
            <ArrowRight aria-hidden="true" />
          </a>
        </Button>
      </section>
    </div>
  )
}
