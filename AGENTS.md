# AGENTS.md

Instructions for coding agents (and people) working on Launch.

## What this is

Launch starts, stops and opens demo environments on a computer, one at a time. `launch.py` is the command; `launcher/` is the Launcher, a local web page on http://localhost:1900/. Each environment is a directory under `environments/` with an `environment.json` and a `run.sh`.

## The design comes first

- How the Launcher and `launch` behave is in `docs/launcher-behaviour.md`. The code follows that document. To change the behaviour, change the document first, with the maintainer.
- Known problems of an environment go in its `environment.json` as `checks`, with a fix that Launch announces before it runs it (plug and play).

## Language

- Texts for the people who use it (the page, `launch`'s messages for now, `environment.json`): Dutch, plain and short, without program errors or exit codes.
- Code, comments, command-line options, commit messages: English.

## Style

- Python 3 standard library only; bash for `run.sh`; one self-contained `launcher.html`.
- Match the surrounding code: short comments that say why, issue numbers like (#284).
- Every file starts with `SPDX-License-Identifier: EUPL-1.2` (in a comment or docstring).
- Works on Windows (Docker Desktop or Rancher Desktop, Git for Windows' bash) and on Linux.

## Tests

- `python -m unittest discover tests` for the unit tests; they do not need Docker or the network.
- `tests/e2e/launcher-behaviour.js` is the browser test of the Launcher.
- Do not start the big environments (Tezza, Valtimo, Nextcloud Common Ground) just to test; Haal Centraal starts in seconds.

## Commits

- Conventional prefix (`feat:`, `fix:`, `docs:`, `release:`, optionally with a scope like `feat(tezza):`), a subject that says what changed for the user, and a body that says why.
- No mention of AI tools or assistants in commits or pull requests: no `Co-Authored-By` lines for them, no "Generated with" lines.
- Commit only when the maintainer asks.
