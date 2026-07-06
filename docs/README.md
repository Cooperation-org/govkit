# GovKit documentation

GovKit is an open-source, multitenant earned-governance toolkit: a team earns ownership and
governance weight from peer-reviewed, approved tasks, and that one earnings record powers
the pie, work-weighted votes, and work-weighted sortition. Start with the
[project README](../README.md) for the overview and current status.

## Guides

- **[Self-hosting](self-hosting.md)** — stand GovKit up with Docker Compose: prerequisites,
  environment variables, bringing up the stack, creating your first org, connecting a Taiga
  task source (both valuation modes), running a drop, viewing the pie, and importing /
  exporting equity. Includes the base-path note for path-prefixed hosting.

- **[Governance practices](governance-practices.md)** — the three mechanisms as a process:
  drops (approved work → issued equity, with per-line adjustment as the correction for
  under-claiming), work-weighted sortition (seeded, reproducible committee draws), and
  work-weighted elections (recorded here, run in the team's existing ElectionRunner — GovKit
  does not build voting-by-email). Explains work-weight and end-to-end traceability.

## Status

Milestone 1 (auth, Taiga adapter, drop engine, pie, import/export) is complete and
integrated. Milestone 2 (votes, sortition, and this documentation) is in progress; the
votes and sortition data models exist while their tally and draw logic are being built.
