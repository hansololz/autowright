# Contributing

Thank you for wanting to improve Autowright. This page covers how to propose
**product ideas and feature requests**. For security reports, see
[SECURITY.md](docs/SECURITY.md). For how the app works today, see [SPEC.md](SPEC.md).

## Product ideas and feature requests

**GitHub Issues** are the intake for product ideas. Discussions are not enabled
on this repo.

**Open an issue** rather than a pull request when the work is still a design
question — for example what to build, why it matters, or how it should feel.
PRs that implement a feature before the maintainer has agreed on direction are
usually harder to review and more likely to be declined.

**GitHub Projects** (this repository's Projects board) are the proposed place for
product idea and feature **planning** after an issue exists: triage, priority,
and roadmap status. Contributors open or discuss via issues; the maintainer
(and anyone they invite) moves accepted ideas through the project. Do not treat
the project board as a substitute for filing a feature request issue.

### Before you open an issue

1. Search existing issues for the same idea.
2. Skim [README.md](README.md) Planned and [SPEC.md](SPEC.md) so the request
   fits the product (local, file-first desktop automations; AI at authoring
   time, not at every run).
3. Prefer one idea per issue.

### What to include

Use the **Feature request** issue template when available, or cover:

- **Problem** — what is painful or missing today?
- **Who it helps** — you, power users, newcomers, teams, etc.
- **Proposal** — the feature or product surface you have in mind (UI, CLI,
  metrics, sharing, …). Rough sketches or examples are welcome.
- **Alternatives** — other approaches you considered, or why existing
  workarounds fall short.
- **Open questions** — privacy, scope, measurement, or trade-offs you want
  the maintainer to weigh.
- **Out of scope (optional)** — what you are *not* asking for, to keep the
  discussion focused.

You do not need a full design or implementation plan. Clear motivation and a
concrete proposal are enough.

### What happens next

The maintainer may:

- Ask clarifying questions
- Accept the direction, add the issue to the repo **Project** for planning, and
  invite a PR (or implement it themselves)
- Defer or decline, with a short reason when possible

Closing an issue does not mean the idea is bad; it may not fit current
priorities or the product's local-first constraints. An idea on the Project
board is a planning signal, not a commitment to ship on a date.

## Bugs and other issues

For bugs, open a separate issue with version, OS, steps to reproduce, and
expected vs actual behavior. Do not mix a large product pitch into a bug
report.

## Code contributions

If you want to contribute code:

1. Prefer an agreed issue (or maintainer thumbs-up) before a large PR.
2. Follow the build and test workflow in [SPEC.md](SPEC.md) §18.
3. Keep PRs focused; match existing style in the files you touch.

Small fixes (typos, clear bugs, docs) are welcome without a prior issue.
