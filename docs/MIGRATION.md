# Migrating from `100x-dev` to `100xprism`

The project was renamed from `100x-dev` to **100xPrism** (npm package, CLI and repo:
`100xprism`). The rename is complete:

- The `100x-dev` npm package is deprecated and points to `100xprism`.
- `github.com/rajitsaha/100x-dev` redirects to `github.com/rajitsaha/100xprism`.
- The `100xprism` package still installs a `100x-dev` command as an alias.

## If you still have 100x-dev installed

```bash
npm rm -g 100x-dev
npm install -g 100xprism && 100xprism install
```

`100xprism install` (and `init`, `update`) migrates legacy state once: `~/100x-dev` moves
to `~/100xprism` with its git remote repointed, and `~/.100x-dev` moves to `~/.100xprism`.
Nothing is overwritten if the new directory already exists. Then run `100xprism init` again
in any project whose generated files you want refreshed, or `100xprism update` to
regenerate every tracked project.

Historical records (`CHANGELOG.md`, `docs/superpowers/`) keep the old name on purpose.
