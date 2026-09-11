# Submission Desk — the React interface

A reviewer-facing client over the HTTP API in `app/api/`. Three pages: the
queue, the review page, and upload. It renders what the use cases return;
nothing here computes a band, resolves a criterion, or moves a run, and every
sentence it shows comes from `/api/vocabulary` so the wording matches the
Streamlit interface.

    make api      # from the repository root: the API on :8000, demo mode on
    make web      # here: Vite on :5173, proxying /api to the API

Or directly: `npm install`, `npm run dev`. `npm run build` writes `dist/`;
set `VITE_API_BASE` at build time to point a static deployment at the API.

Stack: Vite, React 19, TypeScript, Tailwind v4, shadcn/ui on Radix. Routing is
the URL hash (`#/queue`, `#/review/<run id>`, `#/upload`) — deep-linkable, no
router dependency. Dark mode follows the operating system. Status is never
carried by colour alone.

There is no authentication. The API and this client are for one reviewer on
one machine, or behind an identity layer you provide.
