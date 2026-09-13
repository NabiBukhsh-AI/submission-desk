# Submission Desk — the React interface

A reviewer-facing client over the HTTP API in `app/api/`. Five pages: sign
in, the queue, the review page, upload, and settings. It renders what the use
cases return;
nothing here computes a band, resolves a criterion, or moves a run, and every
sentence it shows comes from `/api/vocabulary` so the wording matches the
Streamlit interface.

    make api      # from the repository root: the API on :8000, demo mode on
    make web      # here: Vite on :5173, proxying /api to the API

Or directly: `npm install`, `npm run dev`. `npm run build` writes `dist/`;
set `VITE_API_BASE` at build time to point a static deployment at the API.

Stack: Vite, React 19, TypeScript, Tailwind v4, shadcn/ui on Radix. Routing is
the URL hash (`#/queue`, `#/review/<run id>`, `#/upload`, `#/admin`) —
deep-linkable, no router dependency. Dark mode follows the operating system. Status is never
carried by colour alone.

Everything is behind the one admin account. A fresh database asks the first
visitor to create it; the session is an `HttpOnly` cookie the API sets, so
`fetch` sends credentials and a static build's origin has to be listed in the
API's `CORS_ORIGINS`. The settings page (`#/admin`) sets the provider, its key
(write-only: the page is told a key is set, never what it is), the model and
price per tier, and the reviewer id.
