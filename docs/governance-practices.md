# Governance practices

GovKit supports a specific way of running an organization: **earned governance**. People
earn ownership and a say in decisions by doing peer-reviewed work, and that same earned
contribution is what weights the decisions. This document describes the three mechanisms as
a **process** — what actually happens, step by step. It is not a pitch; it is a description
of the practice the toolkit encodes.

Everything rests on one idea: **work-weight**. A member's weight is their **issued
earnings** over a configurable window (all-time, or a trailing period such as 12 months).
Because the earnings come from approved tasks, weight is not something anyone assigns
directly — it accumulates from reviewed work, and every unit of it traces back to the task
that produced it.

The three mechanisms below all read from that one earnings record.

## 1. Drops — turning approved work into earned equity

A **drop** is a periodic review that converts peer-approved tasks into issued earnings. It
replaces the old ritual of pulling done tasks into a spreadsheet, adjusting them by hand,
and approving them there.

The real-world problem it solves: people often do the work but **under-claim** — they don't
put a value on the task, or they under-value it. So the review step is built around
catching that, by task, rather than trying to remember it per person.

The process:

1. **Value lives on the task.** When a task is created or reviewed, someone authorized sets
   its value — either directly (a tag like `5 cook`) or as hours (with an optional cash
   amount), depending on the org's valuation mode. Claiming credit happens where people
   already work; there is no separate form to fill in.
2. **A steward opens a drop run.** GovKit gathers the eligible tasks — done/approved,
   assigned to a member, and not already part of an earlier drop — groups them by member,
   and computes a value for each line from the org's valuation config.
3. **Review is a queue, not a memory test.** The steward sees each member's total and,
   prominently, a **queue of tasks that are missing a value**. That queue is the correction
   for under-claiming: the tasks that would otherwise be lost are surfaced for a decision.
4. **Adjust with a reason.** A steward can adjust any line up or down. A non-zero
   adjustment **requires a written reason**, and GovKit records who made it and when. The
   common case is adding value for someone who under-claimed. The chain
   `computed value → adjustment (+ reason) → final value` stays visible.
5. **Approve.** Approving the run makes its lines **issued** and **immutable**. A drop run
   is a first-class, permanent object; once approved, its lines are frozen equity that the
   pie and the weighting read from. Nothing edits them after the fact.

Because each issued line keeps its links to the tasks it came from, **every share traces
back to the work that earned it** — the thing a spreadsheet can never give you.

## 2. Work-weighted sortition — drawing a committee

Sortition is selection **by lottery, not by election** — like jury duty. GovKit's draw is
**work-weighted**: a member's chance of being drawn is proportional to their earned
contribution over the configured window. More reviewed work means more weight in the draw,
but it stays a lottery, not a popularity contest.

The property that matters for trust is that the draw is **seeded, auditable, and
reproducible**. A `SortitionDraw` records the number of seats, the weight window, the seed,
and the result — the selected members together with the weights that were used. Anyone can
re-run the same seed over the same weights and get exactly the same outcome. There is no
hidden randomness to take on faith.

The practice this supports (as the team runs it) looks like a small standing committee with
staggered rotation — for example a three-person committee where one seat rotates each
period — drawn by work-weighted lottery from the currently active members. The committee's
job is to prioritize proposals within a budget, working from openly-posted proposals rather
than private lobbying. GovKit's role in that practice is narrow and specific: **it performs
the draw and records it**, reproducibly, from the earnings record. The surrounding rules —
rotation schedule, proposal deadlines, budget allocation, conduct rules — are the team's
governance, not something the toolkit enforces.

> **Status:** the sortition data model is in place; the draw page and the draw logic are
> being built in Milestone 2. This section describes the mechanism the model is designed to
> record.

## 3. Work-weighted elections — recorded, not run here

The team also uses **work-weighted elections** for some roles — for example a ranked-choice,
work-weighted vote for an annual position. The weighting idea is the same: a ballot counts
in proportion to the voter's earned contribution.

**GovKit does not run formal elections.** Those stay in the team's existing email-based
tool (**ElectionRunner**). GovKit does **not** build voting-by-email. The most GovKit does
here is provide a place to **document or record a result** if the team wants it alongside
the earnings record — the actual conduct of a formal election lives outside the toolkit.

What GovKit *does* provide is **informal, live, work-weighted votes** for meetings — a quick
direction check taken in the room, weighted by earned contribution, and recorded. A vote
captures a **weight snapshot** when it opens, so the tally is reproducible even as earnings
later change, and the raw ballots are always kept alongside the weighted result. These are
explicitly meeting votes, **not** formal elections.

> **Status:** the votes data model (including the weight snapshot and raw-ballot retention)
> is in place; the create/vote/tally page is being built in Milestone 2.

## Why work-weight, and why traceability

Two threads run through all three mechanisms:

- **Work-weight** means influence is earned, not granted. Vote weight and draw weight both
  come from the same issued earnings, over a window the org chooses. Nobody sets weights by
  hand; they follow from reviewed work.
- **Traceability** means the record is auditable end to end. Every issued unit traces to a
  drop line, every drop line to the tasks behind it and any adjustment (with its reason and
  author). A committee draw traces to a seed and the weights it used. A meeting vote traces
  to the weight snapshot taken when it opened. The point of the system is that you can
  always answer "why does this number look like this?" — and get a real answer.

## See also

- [Self-hosting guide](self-hosting.md) — setting up an org, valuation modes, running a drop.
- [Project README](../README.md) — what GovKit is and its current status.
