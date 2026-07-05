<!-- Kubo PR template. Commits and PRs in English (D16). Delete lines that don't apply. -->

## What

<!-- The change, in one or two sentences. -->

## Why

<!-- The reason. Link the session plan / issue driving it. -->

## TDD evidence (RED → GREEN)

<!-- Show the cycle happened: the failing test first, then the code that made it pass.
     Paste the RED output or link the commits (test: … → feat: …). Docs-only PRs: N/A. -->

## Quality gates

- [ ] `ruff check` + `ruff format --check`
- [ ] `pyright`
- [ ] `pytest` (unit; integration if touched)
- [ ] `uv lock --check`

## Checklist

- [ ] ADR touched / added? Which one, or N/A.
- [ ] Session plan of origin: `docs/sessions/NNNN-….md`
- [ ] New dependency? Justify it here (or N/A).
