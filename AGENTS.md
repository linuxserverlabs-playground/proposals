# AGENTS.md - proposals

The LinuxServer Labs proposal form and its automatic check. Workspace-wide rules are in the playground workspace's
AGENTS.md; the Labs design is its `docs/labs.md`.

## What is here

- `.github/ISSUE_TEMPLATE/proposal.yml`: the issue form. Its field labels and bar options are a contract: the check,
  the harness's propose-image playbook and `labs try`/`labs accept`, and the Labs site all parse submissions by the
  label text (GitHub renders a submission as `### <label>` blocks, an empty answer as `_No response_`). Never
  rename a label or option without changing every consumer in the same push.
- `scripts/check_proposal.py`: run by `.github/workflows/check.yml` on opened, edited and reopened issues labelled
  `proposal` (or by `workflow_dispatch` with an issue number). Standard library only, `GITHUB_TOKEN` only. It keeps
  one comment, found by the `<!-- labs-proposal-check -->` marker, and sets exactly one of `meets-bar` and
  `needs-info`.
- `tests/`: unit tests with real-looking form bodies in `tests/fixtures/`, and a fake API client.

## Rules

- The issue body is untrusted. It never reaches a shell or an expression in a workflow; the script reads the event
  payload file. Echo answers only through `code()` (one line, in a code span, so no mentions or broken tables) and
  link logins instead of mentioning them.
- An API failure is reported as "Could not run" and labels the issue `needs-info`; it never passes a check.

## Validate before pushing

```bash
python3 -m unittest discover -s tests
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest -no-color
```
