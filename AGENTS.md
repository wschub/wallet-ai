# Working rules for AI agents (wallet-ai)

- Never run mutating git commands (commit, add, stash, switch, checkout, branch, reset, rebase, merge, push). Read-only git is fine. If a git write seems necessary, say so and stop.
- Never run /speckit.implement without an explicit scope in the prompt. Do only the named tasks and create no files outside the stated scope.
- You have no memory of earlier sessions. Files in the working tree that you do not recognize were probably written by you earlier: report them as "unknown origin", never as "not mine", and do not build on them.
- TDD: show each new test failing for the right reason before implementing.
- Database: use only the DATABASE_URL given in the prompt. Never disable triggers, change grants or delete rows in append-only tables. Mutation testing runs only on the disposable database named in the prompt.
- Never weaken an existing test (assertions, matches, parametrization) without stating exactly what changed and why.
- Report only what you verified in this session, with the exact commands and output for lint-imports, ruff and pytest.
