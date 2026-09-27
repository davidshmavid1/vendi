<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

The Next.js project root is `frontend/`; its bundled documentation is in
`frontend/node_modules/next/dist/docs/`. Run Node/Next.js/Prisma commands from
`frontend/`. The Django project root is `backend/`.

## Vendi engineering preferences

- Before designing or implementing the landing page, read the saved user brief
  in `docs/design/landing-page.md`. It is future design direction, not a request
  to implement it during unrelated work.

- Preserve the working application. Make the smallest cohesive change that
  fully satisfies the requested scope; avoid unrelated cleanup, broad rewrites,
  speculative abstractions, and unnecessary dependencies or file moves.
- Inspect the working tree before editing. Preserve unrelated and staged user
  changes. Do not reset, discard, or overwrite work to simplify a task.
- Keep changes incremental and reviewable. For migrations, prefer additive,
  backward-compatible steps and an explicit cutover and rollback plan. Do not
  drop data, replace working subsystems, or change production resources without
  explicit authorization for that operation.
- Follow pragmatic object-oriented design: give classes focused responsibilities,
  encapsulate state and business invariants, prefer composition over inheritance,
  and keep infrastructure concerns separate from business rules. Use classes
  where they clarify behavior; do not wrap simple functions in classes or add
  inheritance, interfaces, or design patterns without a concrete need.
- Respect framework conventions and existing architecture. Do not convert the
  existing functional TypeScript domain layer solely to enforce OOP. Apply these
  preferences to new work and necessary changes without expanding the scope.
- Verify behavior with checks appropriate to the affected area, including
  regression tests for permissions, payments, and data integrity when changed.
  Report checks actually run, remaining risks, and any unverified behavior.
- Outside the phase workflow below, do not commit, push, or open a pull request
  unless the user explicitly authorizes them. Deployments, PR merges, and
  destructive database operations always require explicit authorization.

## Phase completion workflow

- A request to implement a Vendi phase authorizes the agent to edit files,
  run relevant checks, commit the phase changes, push a feature branch to this
  repository's configured remote, and create a pull request without asking for
  additional confirmation. Merely asking to write or plan a phase prompt does
  not authorize implementation.
- This standing authorization replaces the approval-only commit/push/PR wording
  in previously written phase prompts. Honor any new, explicit request to leave
  changes uncommitted or not push or create a PR.
- Keep the work scoped to the phase. Review the diff and stage only its changes;
  preserve unrelated and previously staged work. Never include secrets or local
  IDE files. Use a feature branch (default prefix `codex/`), never push directly
  to the default branch, and do not force-push.
- Run the relevant checks before committing. Resolve failures caused by the
  phase. If required verification is blocked, describe the limitation and open
  a draft PR rather than presenting the phase as fully verified.
- Use a descriptive commit and PR title identifying the phase and its changes.
  The PR description must explain the resulting behavior, migrations if any,
  checks actually run, and remaining limitations. Reuse an existing PR for the
  same phase branch instead of creating a duplicate.
- Return the PR link when finished. Do not merge the PR or deploy the changes
  as part of this authorization.
