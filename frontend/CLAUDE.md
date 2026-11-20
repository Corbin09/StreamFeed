# frontend/CLAUDE.md

Guidance for work inside `frontend/` (Next.js App Router, Tailwind v4, React Query). The
root `CLAUDE.md` still applies. Layout and conventions are in `README.md` next to this file;
read it first.

Rules that are easy to get wrong:

- **URL = feed state.** Search params are sent to `GET /posts` unchanged, so filter keys
  must equal API parameter names. Never hard-code filters: the sidebar is generated from
  `GET /meta/categories`.
- All pages are client components that call `/api/*` on their own origin. `next.config.ts`
  rewrites it to `API_INTERNAL_URL`, which is fixed **at build time** (`http://localhost:8000`
  in dev, `http://backend:8000` in the Docker image). Changing it needs a rebuild.
- Basic Auth lives in `src/middleware.ts` (Node.js runtime) and is active only when
  `BASIC_AUTH_USER`/`BASIC_AUTH_PASSWORD` are set at runtime.
- Colors come from the CSS variables in `src/app/globals.css` (light + dark); use the
  Tailwind token names (`paper`, `ink`, `jade`...), not raw hex values.
- UI text is English, sentence case. Post content is shown unchanged with its `lang`.
- Font: Be Vietnam Pro with the `vietnamese` subset (needed for accented text).

Checks: `npm run lint && npx tsc --noEmit` (or `../scripts/run.sh check`). Dev server:
`npm run dev` with the API on :8000 (`../scripts/run.sh up` provides it).
