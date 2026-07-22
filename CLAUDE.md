@AGENTS.md

## Working agreement

- Do not write file edits to disk (Edit/Write tool calls) and do not push a
  branch or open a pull request, without first showing the user the exact
  change and getting explicit confirmation to proceed.
- This does not apply once the user has explicitly said to proceed in "auto
  mode" for the current task — in that case, act without pausing for
  per-change review as usual.
- Local, unpushed commits on a dedicated branch are fine to create as part of
  proposing a change; the review gate is about writing to disk and about
  anything that leaves the local machine (push/PR).
