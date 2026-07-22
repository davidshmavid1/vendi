@AGENTS.md

## Working agreement

- Show the user the exact proposed change and get explicit confirmation
  before writing file edits to disk (Edit/Write tool calls).
- Once written to disk, leave changes uncommitted — do not run `git commit`
  — so the user can review the diff in their IDE's own git tracker. Only
  commit when the user explicitly says to.
- Do not push a branch or open a pull request without explicit confirmation.
- This does not apply once the user has explicitly said to proceed in "auto
  mode" for the current task — in that case, act without pausing for
  per-change review as usual.
