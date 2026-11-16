# StreamFeed frontend

Next.js (App Router) + Tailwind CSS v4 + React Query. It only talks to the REST API
(`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`). See `docs/ARCHITECTURE.md` §9.

```bash
npm install
npm run dev     # http://localhost:3000, needs the API running
npm run lint && npx tsc --noEmit
npm run build
```

## Layout

- `src/app/page.tsx`: feed (`components/feed/FeedView.tsx`).
- `src/app/posts/[id]/page.tsx`: post detail.
- `src/app/admin/sources/page.tsx`: sources, "Run now" and recent runs.
- `src/lib/api.ts`: typed API client; `src/lib/hooks.ts`: URL-backed feed state, save/hide mutation.
- `src/components/PayRail.tsx`: salary bar on a shared USD scale.

## Conventions

- **Feed state lives in the URL.** The page's search params are sent to `GET /posts` unchanged, so
  filter keys must match the API's parameter names. The sidebar is built from `GET /meta/categories`;
  a new attribute filter in the backend schema shows up without frontend changes.
- **Design tokens** are CSS variables in `src/app/globals.css` (light and dark), exposed to Tailwind
  as `paper`, `surface`, `ink`, `muted`, `faint`, `line`, `track`, `jade`, `saffron`, `vermilion`.
  Jade is for pay and primary actions, saffron for "new since last visit", vermilion only for problems.
- Typeface: Be Vietnam Pro (latin + vietnamese subsets) so scraped Vietnamese text renders correctly.
- UI text is English and sentence case; post content is shown unchanged with its `lang` attribute.
