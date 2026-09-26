<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Vendi engineering preferences

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
- Do not commit, push, deploy, or perform destructive database operations unless
  the user explicitly authorizes them.
