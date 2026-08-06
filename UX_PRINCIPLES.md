# UX principles

Golda's rules for anything a person looks at. Same file in `amebo`, `workers.vc`
and `govkit` — one set of rules, three repos. Quoted lines are her words.

Judge every change by what the person experiences first. If a change is smooth,
clean, fast and easy, it is right; if it adds a click, a redirect, a dead end or
a paragraph, it is not.

---

## 1. Omit needless words. Show, don't tell.

> "Omit Needless Words. Show Not Tell." · "no noise" (golda, 2026-08-06)

Be concrete, be precise, cut the rest. No labels that restate the obvious, no
helper prose, no preamble.

A new thing arrives small and quiet: two faces in a corner, not a box with a
label and a button. The control is the instruction. **If you have to write
instructions, it is too late** — go back and make the thing obvious instead.

Show only what reflects an intention the person has or an opportunity they might
want. Never instructions.

DRY applies to people too: never say the same thing twice on one screen, and
never repeat back what they just did.

**Banned outright:** "we don't just X, we Y" · "not just… but" · dramatic
em-dash reveals · empty intensifiers (lived, credible, concrete, powerful) ·
clipped comma-spliced fragments ("six quote slots, yours, article can't ship").

> "if i have to see ai'isms like this ... i will NEVER USE THIS TOOL"

> "SHOW not tell — if it did something useful SHOW me and let me correct it or
> do something with it. NEVER textwall at me about what amebo did."

Show the thing, never a report about the thing. If the agent drafted an email,
the email is on screen, editable. No digest of its own activity, no "drafted and
waiting", no summary of work done.

Anything cut can still pop out on request: the person asks, it opens. What is
wrong is a quiet face until they click it.

## 2. Everything actionable

> "make eveything i see editable or actionable"

Nothing on screen exists only to be read. Every element is a thing you can press,
follow, or change.

## 3. Links

> "LINKS to the things, previews, brief, as links, not 'drafted and waiting'
> that is so sycophantic and annoying just THE THINGS"

If it cannot be shown, link it. `#34 Apply: NCBA IMPACT 2026 ↗`, not "the task
has been created for you."

## 4. If I can see it, I can edit it

> "everything i see, can edit, quick not too many clicks"

Every visible field is editable where it sits, whenever the source system allows
it at all. A read-only line next to editable ones is a bug. Use the real control:
a status is a dropdown of that board's own statuses, an assignee is a dropdown of
that board's own members — never a free-text box that fails on a typo.

## 5. Save in the flow

> "save easily and in the flow"

Edit in place, save on leaving the field. No edit mode, no Save button to hunt
for. Offer an explicit Save anyway for people who would rather press something.

## 6. Their words lead

> "in the needs thing it should have QUOTES from the user and a LINK to where
> they asked. elevate the HUMAN words"

Lead with what a person actually said, named, with a link to where they said it.
`Peter: ok dns is ready on my side ↗` — name, colon, their words. Never an
indented block quote with an attribution line underneath.

## 7. The agent is invisible

> "tell amebo to mostly be invisible, shut it up! ai's are SUPER ANNOYING when
> they textwall"

## 8. Assembling beats writing

> "helper does not always have to write words for me sometimes its just
> assembling the context and links conveniently"

Most of the time the contribution is the quote, the task, the link and the file
gathered in one place. Generating text is the exception.

## 9. Don't box it in

> "i hate the idea of being limited but it might be good to have a task take up
> most of screen when popped up"

When something opens, give it room — most of the screen, one key to leave. A
cramped inline strip is worse than a link out.

## 10. Mark what was found, say what is invented

> "if amebo found it put like (?) and if it was in the ticket leave out (?)"

A link already on the record stands plain. A link the agent went and found gets
`(?)`, so the reader knows which to distrust. Mock data in a mockup is labelled
as mock, on the page. A plausible fake quote attributed to a real colleague is
worse than an empty box.

## 11. Fail forward, never a dead end

No redirect chains, no 404 where a forward would do. A page that has been folded
into another forwards into it.

## 12. Later needs a when

> "We're snoozing the task, not snoozing the claw."

Dismissing is not deciding. Pushing something out writes a new date on the thing
itself, so it comes back on its own. No snooze state in the agent.

## 13. Deadlines raise rank, they don't get their own box

> "it can be mixed just deadlines are like increase the ranking"

One ranked list. Say which half of the ranking put each item where it is: a rule
(dated, deterministic, needs no defending) or a judgement (which must show its
reason).

## 14. High guards on acting alone

> "if its clear what to do, it can just do it, but if it involves communicating
> with a human in slack or anything possibly destructive, it should check"

Acts alone: reversible, touches no person. Always asks: anything a human will
read, anything destructive, anything it is guessing at. A human pressing the
button is not the agent acting alone — do not gate that.

---

## Working this way

- **Show a design, don't describe one.** "this is why i needed to SEE it. your
  conception of words and mine are completely different." Build the picture,
  put it on a URL, then talk.
- **Proposals before builds** when the shape is not settled.
- **Never invent an element and show it as real.** Say which parts do not exist.
- **Questions in concrete terms, 50 words or less, no internal labels.** "if you
  dont' really know what you mean" you cannot ask it plainly.
- **Answer the question that was asked.**

Longer draft with the full session history:
`~golda/work/7-25-2026-how-to-design-together.md`. Worked example:
`demos.linkedtrust.us/claw-list-proposals/`.
