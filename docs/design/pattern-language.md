# The LinkedTrust Pattern Language

*A design language for the LinkedTrust family — GovKit first. Written as a pattern
language in Christopher Alexander's sense: named patterns at every scale, each solving a
recurring problem, each connecting up to larger patterns and down to smaller ones. You
don't apply it all at once; you grow a page pattern by pattern, and each addition should
heal the whole.*

> **Vendored copy.** The canonical home of this language (and of the token sheet) is
> `Cooperation-org/site-linkedtrust-us` → `design/` — edit there first, then sync this
> copy and `static/govkit.css`. GovKit keeps its own copies because it is self-hostable
> and must work offline.

---

## The philosophy — the quality without a name, here

These products are about people cooperating: work done, reviewed by peers, becoming
ownership you can trace with your finger. The feeling we are after is a **thriving tree** —
a live oak: a dark, patient trunk, many fine branches, and leaves at the tips catching
light. Growth that happened slowly and honestly, ring by ring, nothing bolted on.

The LinkedTrust logo already says it: a dark plum trunk branching upward inside a circle,
leaves at the tips in cyan, teal, coral, green, and gold. Every org is a tree. Every
member is a leaf. Every share of the pie traces down a branch to the work that grew it.

The material is **paper and ink** — a ledger kept at a co-op's kitchen table, not a SaaS
dashboard. Warm off-white paper, dark warm ink, hairline rules, numbers in even columns.
Color is scarce and always *means* something: a member, a status, a live thing growing.

And it is **quiet**. The content is the meat; everything else is at most a frame. Small
quiet buttons. No gradients on actions, no banners shouting, no decoration that doesn't
arise from structure, and **never any animation** — a ledger does not blink. When almost
everything whispers, the one thing that matters can speak in a normal voice and be heard.

Three tests for any new piece of UI:

1. **Would it survive being printed?** (Paper-and-ink structure, not effects.)
2. **Does it trace?** (Every number expandable to the work behind it.)
3. **Is it quieter than the content?** (If the chrome competes with the meat, cut it.)

---

## How to use this language

Each pattern below is written as: **name** · *the problem* · **the resolution** · what it
serves (↑) and what completes it (↓). Larger patterns first. When you build or change a
page: pick the composition pattern that fits, and let it pull in the smaller ones it
needs. When something feels wrong, find the pattern it violates before inventing a fix.

The language is **themeable at the roots**: every color, radius, and font is a CSS custom
property (`static/govkit.css`), so each org — and each sibling app in the family — can
make it their own without leaving the language, the same way GovKit lets each org name
its own unit of value.

---

## I. Ground patterns (the material)

### 1 · WARM PAPER, DARK BARK

*Cold gray-blue surfaces make cooperation software feel like an accounting SaaS; pure
white glares.* — Set the world on **warm paper**: off-white with a drop of honey
(`#faf9f6`), cards a half-tone lighter, hairlines in warm gray. Dark mode is not inverted
paper but **bark**: deep warm brown-black (`#171310`), the tree at night. Both modes stay
in one warm family so the leaves (pattern 6) read the same against either.
↑ serves the philosophy · ↓ completed by HAIRLINE STRUCTURE, SIX LEAVES.

### 2 · INK, NOT PAINT

*When color is used for decoration, it stops being able to mean anything.* — Text,
rules, and structure are **ink**: warm near-black on paper, warm off-white on bark.
Color appears only when it carries meaning: a member's leaf, a status word, a link, the
one live action. If a screen is colorful, something is wrong.
↑ WARM PAPER · ↓ SIX LEAVES, STATUS IS A WORD.

### 3 · QUIET TYPE, SPOKEN HEADINGS

*A single geometric sans everywhere reads corporate; display fonts read loud.* — Body
text is the reader's own **system sans** (fast, familiar, CSP-safe, no font files).
Headings are a **serif with book warmth** (Charter/Georgia stack) — the voice of a person
speaking at the table, not a brand shouting. Numbers in ledgers and anything
machine-true (hashes, seeds, slugs) wear a **monospace** with tabular figures — the
LinkedTrust site already speaks this `// mono` accent; here it marks what the machine
will faithfully reproduce.
↑ INK, NOT PAINT · ↓ LEDGER TABLES, A DRAW YOU CAN VERIFY.

### 4 · NOTHING MOVES

*Animation steals attention from content and makes an honest ledger feel like a pitch
deck.* — **No animation, ever.** No transitions, no fades, no spinners that dance, no
carousels. State changes are instant and legible: a thing is open, then it is approved.
The page holds still so the reader's eye does the moving.
↑ the philosophy directly · ↓ everything; this pattern has no exceptions.

### 5 · HAIRLINE STRUCTURE

*Drop shadows and "elevation" simulate a screen hovering over a screen — placeless;
heavy borders make boxes shout.* — Structure comes from **1px warm hairlines** and
spacing, the way a ledger is ruled. Cards are paper on paper: a hairline and a breath of
space, no shadow (at most a 1px tint under sticky chrome). Radius is small and honest —
6px controls, 10px cards — soft as worn paper corners, not bubbles.
↑ WARM PAPER · ↓ LEDGER TABLES, SMALL QUIET BUTTONS.

### 6 · SIX LEAVES (the categorical palette)

*Identity colors picked ad hoc drift, clash, and fail colorblind readers.* — Exactly
**six leaf colors**, taken from the logo's leaves and deepened until they pass all six
palette checks (lightness band, chroma floor, CVD ΔE ≥ 12 adjacent, ≥3:1 contrast) on
both paper and bark — run `dataviz`'s `validate_palette.js` before ever changing them:

| leaf | on paper (light) | on bark (dark) | from the logo's |
|---|---|---|---|
| cyan | `#007fa3` | `#2ea3c9` | cyan `#00b2e5` |
| coral | `#c94f58` | `#d8626d` | coral `#ff6872` |
| green | `#5c8a27` | `#6f9c3c` | leaf green `#8dc63f` |
| plum | `#9d4270` | `#c06a94` | trunk plum `#3f2534` |
| gold | `#a3780a` | `#b8892a` | gold `#ffca4d` |
| teal | `#009b83` | `#25a08e` | teal `#00d0db` |

Assign in **fixed order, by entity, never re-painted** when a filter changes the set. A
seventh member is not a seventh hue — the cycle repeats with the leaf *shape* and label
carrying identity (color is never the only signal). Cyan doubles as the link/accent
color; green, gold, and coral double as the status family (pattern 12).
↑ INK, NOT PAINT · ↓ EVERY MEMBER A LEAF, THE PIE IS A CANOPY.

---

## II. Living patterns (people and growth)

### 7 · EVERY MEMBER A LEAF

*People rendered as gray initial-circles look like inventory.* — A member's mark is a
**leaf**: a small shape with three round corners and one stem corner
(`border-radius: 50% 50% 50% 2px`), tinted with their leaf color (soft fill, deep
initial). The same leaf appears as their swatch in the pie, their chip in a ballot, their
seat in a committee — one person, one leaf, everywhere.
↑ SIX LEAVES · ↓ THE PIE IS A CANOPY, SEATS AT THE TABLE.

### 8 · PROVENANCE IS A BRANCH

*A number you can't trace is a claim, not a record.* — Every aggregate opens **downward
along a branch**: a quiet disclosure (`details`) whose expanded body is indented behind a
1px branch-line, ending in the leaves — the actual tasks, drops, and opening balances
that grew the number. Drill-down is typography and rules, never a modal, never a page
away. This is the honest-ledger aesthetic: the trace *is* the ornament.
↑ the philosophy (does it trace?) · ↓ LEDGER TABLES.

### 9 · THE MEAT IS THE PAGE

*Dashboard chrome — banners, stat-tile walls, marketing heroes — crowds out the actual
record.* — Content sits in **one readable column** (~62rem), starts immediately (an h1
and one sentence of context, then the record itself), and owns the page. No hero
sections, no decorative sidebars, no cards-for-cards'-sake. Chrome is one thin header
and one thin footer.
↑ the philosophy (quieter than the content) · ↓ ONE TRUNK PER PAGE.

### 10 · SMALL QUIET BUTTONS

*Big filled buttons everywhere turn software into a fairground.* — Actions are **small
and bordered**: hairline border, paper fill, ink label, small type (0.85rem). At most
**one filled action per page** — the season's action, the thing this page exists to do
(open the run, cast the ballot, draw the committee) — filled in deep accent, still small.
Destructive actions are coral-bordered, never coral-filled. Links are links (accent ink,
underline on hover), not button costumes.
↑ THE MEAT IS THE PAGE, NOTHING MOVES · ↓ completes every composition below.

### 11 · LEDGER TABLES

*Data tables styled as apps (zebra stripes, heavy chrome) obscure the numbers.* — A
table is a **paper ledger**: hairline rules between rows only, header in small caps-ish
muted ink, amounts right-aligned in tabular monospace figures with the **unit named** at
the head (the org's own unit — "1,250 slices"). Totals rule off with a slightly stronger
line. A row's meaning should survive photocopying.
↑ HAIRLINE STRUCTURE, QUIET TYPE · ↓ PROVENANCE IS A BRANCH.

### 12 · STATUS IS A WORD, NOT A COLOR

*Colored dots alone exclude colorblind readers and lie when palettes drift.* — Status is
always a **word in a quiet tint**: `open` (gold tint), `approved`/`live` (green tint),
`closed` (ink tint), `failed` (coral tint). Tint means: pale wash background, deep text,
no border. The word does the work; the tint just lets you find it fast.
↑ SIX LEAVES · ↓ used in every composition.


### 19 · DETAILS UNFOLD

*A page that shows every option and every field up front is intimidating; a wizard that
hides them across steps loses the whole.* — Show the **high-level concept first, details
if you want them** — all in one place. The essential few fields (or the headline numbers)
are visible; everything advanced sits folded beneath a one-line summary that names the
concept AND its defaults ("Valuation — hours × rate, all-time window"), so skipping it is
an informed choice. A fold with a validation error opens itself. PROVENANCE IS A BRANCH
is this pattern applied to a ledger; this is the general law for forms and pages alike.
↑ THE MEAT IS THE PAGE, ONE TRUNK PER PAGE · ↓ HAIRLINE STRUCTURE, `details.gk-unfold`.

---

## III. Compositions (whole pages)

### 13 · ONE TRUNK PER PAGE

*Pages that try to do three things do none.* — Each page has **one trunk**: one h1
(serif, spoken), one purpose, at most one filled button. Everything else on the page is
a branch off that trunk, reachable by reading downward. The four GovKit surfaces are four
trunks: Drops (the earning ritual), Pie (what grew), Votes (deciding together),
Committee (who stewards next).
↑ THE MEAT IS THE PAGE · ↓ the four surface patterns below.

### 14 · THE PIE IS A CANOPY

*A pie chart with a dozen wedges and a detached legend is unreadable.* — Shares render
as a **stacked bar** (the canopy in cross-section): segments in leaf order with a 2px
paper gap between them, each ≥1px so no one disappears. Below it, the ledger: each row a
member with their leaf, their amount in the org's unit, their share, and a quiet row-bar
on a shared scale. Every row opens along a branch (pattern 8) to the drops and tasks
that grew it. Weighted and raw never share an axis with different scales.
↑ ONE TRUNK, SIX LEAVES, EVERY MEMBER A LEAF · ↓ PROVENANCE IS A BRANCH, LEDGER TABLES.

### 15 · TWO TALLIES, SAME RULER

*Showing weighted results alone invites distrust of the weighting.* — Vote results show
**both tallies side by side on the same 0–100% ruler**: the work-weighted bar (accent)
and the raw-count bar (muted ink), labeled in words. Honesty is showing the difference,
not hiding it. The winner is stated in a sentence, not a trophy graphic.
↑ ONE TRUNK · ↓ LEDGER TABLES, STATUS IS A WORD.

### 16 · A DRAW YOU CAN VERIFY

*A committee draw people can't reproduce is just an announcement.* — The sortition page
leads with the human result — **seats at the table**, each drawn member as a leaf card —
and keeps the machinery legible right below: seed, snapshot, and weights in monospace
(pattern 3), with the verify panel a branch away. Reproducibility is the ornament here;
show it plainly.
↑ ONE TRUNK, EVERY MEMBER A LEAF · ↓ QUIET TYPE (mono).

### 17 · THE DOOR IS PLAIN

*Login pages dressed as billboards promise a product, not a place.* — The way in is a
**plain door**: the mark, the name in serif, one sentence of what this place is, the
sign-in form. Centered, small, paper-quiet. You are entering someone's workshop, not a
funnel.
↑ THE MEAT IS THE PAGE · ↓ SMALL QUIET BUTTONS.

### 18 · THEMEABLE ROOTS

*A design system that can't be owned gets forked or ignored.* — Everything above ships
as **CSS custom properties on `:root`** with a dark override, in one dependency-free
stylesheet. An org (or a sibling app — amebo, the LinkedTrust site) re-roots the tree by
overriding tokens: its own accent, its own paper, even its own leaf set (revalidate!),
without touching a selector. The language is the shared part; the theme is theirs — like
naming their own unit of value.
↑ the whole language · ↓ `static/govkit.css` is the reference implementation.

---

## Applying it elsewhere in the family

The core layer (tokens + ground patterns + components) is deliberately brand-neutral-ish:
it encodes *warm paper, ink, hairlines, six leaves, quiet buttons, no animation*. The
LinkedTrust site itself keeps its hand-designed identity (the logo, the warm light base,
the `// mono` labels are already this language); if we later re-ground the site in these
tokens, patterns 1–12 apply unchanged and the compositions get rewritten for its content.
What we do **not** import from the current site: gradient-filled CTAs, animated
carousels, glassmorphism tabs — those fail NOTHING MOVES and SMALL QUIET BUTTONS.
