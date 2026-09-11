# occupancy_tracker

`BEHAVIOR.md` is the binding specification for how this integration behaves.
Read it before touching code, tests, or configuration, and follow it strictly.

- A behavior change starts in `BEHAVIOR.md` (record the household decision in
  its section 13), then the tests, then the code, then the 91-day replay.
  Section 16 gives the order and the commands.
- Where the code and `BEHAVIOR.md` disagree, the code is wrong unless
  `BEHAVIOR.md` section 15 lists the difference. Do not fix a difference that
  section 15 marks as accepted or as needing a household decision.
- `ARCHITECTURE.md` maps the components; `README.md` is the user guide.
  Neither overrides `BEHAVIOR.md`.
- Never deploy to the Home Assistant host or restart Home Assistant without
  the user's explicit approval in the current conversation. The deployment
  procedure is in the parent workspace's `DEPLOYMENT_SOURCES.md`.
- This repository is public: keep host addresses, SSH details, and other
  household identifiers out of files, commits, and pull request text.
- Run `uv run ruff check . && uv run ruff format --check . && uv run pytest`
  before calling a change done.
