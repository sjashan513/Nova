# Nova Skill — Dev Domain
## Rules for planning tasks in the coding domain

These rules apply to any task involving TypeScript, Python, or general
code manipulation. Follow them exactly — they encode hard-won patterns
that prevent common planning mistakes.

### File reading
- Always read a file with `filesystem.read` BEFORE passing it to any
  worker that needs `file_content`. Never assume a worker can read
  the file itself — Workers think, Tools do I/O.
- A `filesystem.read` step can run in parallel with a `worker_ts_check`
  step if both have no shared dependencies. Do this when possible.

### TypeScript error fixing
- Always run `worker_ts_check` BEFORE `worker_ts_fix`. The fix worker
  needs the structured error list from the check worker.
- `worker_ts_fix` self-verifies its output by running tsc internally.
  You do NOT need to add a verification `worker_ts_check` step after
  `worker_ts_fix` — it is redundant. Only add a final `worker_ts_check`
  if you want to confirm the state of the file for the user.
- Always pass `file_path` to `worker_ts_check` when fixing a specific file.
  This filters tsc output to only that file's errors — without it,
  errors from other files in the project contaminate the result.

### JSDoc
- Always run `worker_ts_fix` BEFORE `worker_jsdoc` if there are known
  TypeScript errors. JSDoc must be added to a clean file — adding
  documentation to a file with type errors produces unreliable results.
- `worker_jsdoc` input `file_content` must reference the FIXED content
  from `worker_ts_fix` (i.e. `$<fix_step>.fixed_content`), not the
  original file read. This ensures JSDoc runs on the corrected version.

### Writing back to disk
- Always write the FINAL transformed content to disk with
  `vscode.show_diff` as the last step that modifies a file.
- If both `worker_ts_fix` and `worker_jsdoc` run on the same file,
  write the output of `worker_jsdoc` (documented_content), not the
  output of `worker_ts_fix` (fixed_content) — the jsdoc output
  already contains the fix.

### Step ordering for a typical fix + document task
```
s1: filesystem.read        (read original file)
s2: worker_ts_check        (detect errors)       [parallel with s1]
s3: worker_ts_fix          (fix errors)           [depends_on: s1, s2]
s4: worker_jsdoc           (add JSDoc)            [depends_on: s3, input: $s3.fixed_content]
s5: fvscode.show_diff      (propose change via diff gate)     [depends_on: s4, input: $s4.documented_content]
```