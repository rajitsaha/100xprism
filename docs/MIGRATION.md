# Migration: `100x-dev` → `100xPrism`

Rebrand from the generic `100x-dev` (collides in search with the established
[100xdevs](https://100xdevs.com/) brand and the "100x developer" meme) to the
distinctive, ownable **100xPrism** — one config split into native setup for every
AI coding tool, the way a prism splits one beam into a full spectrum.

- **Display brand:** 100xPrism
- **npm package / CLI / repo:** `100xprism` (lowercase — npm requires it)
- **Availability (verified):** npm ✅ · GitHub repo/org ✅ · `100xprism.com` / `.dev` / `.io` ✅

## Golden rule of ordering

Stand up the **new** home and the **fallback shim** *before* touching the old
name. Never leave a window where an existing user hits a dead end.

---

## Phase 0 — Reserve (non-destructive)
- [ ] Register `100xprism.com` before publicizing the name
- [ ] Do not rename anything yet

## Phase 1 — Stand up the new package
- [ ] Internal rename pass: `100x-dev` → `100xprism` across `package.json`, `bin/`,
      install scripts, README, live docs, runtime libs, default clone path
      (`~/100x-dev` → `~/100xprism`), and all `github.com/...` / `raw.githubusercontent.com/...` URLs
- [ ] Keep a **`100x-dev` alias bin** alongside `100xprism` so old muscle memory /
      scripts keep working for a release or two
- [ ] Make the installer **self-migrate**: detect an existing `~/100x-dev`,
      `git remote set-url origin <new>`, optionally move the directory
- [ ] Update tests asserting the old strings; `npm run check` must stay green
- [ ] Publish `100xprism` to npm

## Phase 2 — Redirect the old npm package
- [ ] Publish one final `100x-dev` shim release that prints
      `⚠️ 100x-dev is now 100xPrism → npm i -g 100xprism` (delegate or exit)
- [ ] `npm deprecate 100x-dev "Renamed to 100xprism — install: npm i -g 100xprism"`

## Phase 3 — Rename the repo (auto-redirects)
- [ ] Rename `rajitsaha/100x-dev` → `rajitsaha/100xprism` in settings
      (stars / issues / PRs / clones follow automatically)
- [ ] **Never** recreate a repo named `100x-dev` — that is the only thing that
      breaks GitHub's permanent redirect

## Phase 4 — Docs & raw URLs
- [ ] Update the `get.sh` curl one-liner, README badges, and any
      `raw.githubusercontent.com/.../100x-dev/...` links — do **not** rely on raw redirects

## Phase 5 — Pages + Search Console
- [ ] Pages auto-moves to `rajitsaha.github.io/100xprism/`
- [ ] Re-verify the new property in Search Console + resubmit `sitemap.xml`
- [ ] Update the landing page copy/schema to `100xPrism`

## Phase 6 — Visual assets (brand artwork)
The raster brand images bake in the old "100x Dev" wordmark and must be
regenerated — a `sed` cannot touch pixels:
- [ ] `assets/100x-dev-blogo.png` — primary logo (referenced in README + landing page)
- [ ] `assets/postcard-stack.png` — README hero PNG
- [ ] Other `assets/*.html` postcard sources + any LinkedIn/release banners
- [ ] Rename the files to `100xprism-*` and update every reference

> These need a design tool or designer for polished output. A prism mark
> (one beam in → rainbow spectrum out) is the natural motif and reinforces the
> product story.

---

## What is intentionally NOT changed

Historical records keep the old name — rewriting them would falsify the project's
history:
- `CHANGELOG.md`
- `docs/superpowers/specs/*` and `docs/superpowers/plans/*` (dated design docs)
- Past-tense entries in `ROADMAP.md` / `docs/v2-refactor.md`
