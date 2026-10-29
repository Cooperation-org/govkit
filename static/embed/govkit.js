// GovKit embed bundle v0 (PLAN-cohort-dash.md item 5)
//
// Vanilla-JS custom elements for the cohort dash (and any host page). Follows the
// amebo embed pattern (amebo/embed/amebo.js): one static file, zero dependencies,
// no build step, no shadow DOM — the host styles via tag selectors / CSS variables.
//
//   <govkit-pie>        the org equity pie + legend        (pie/orgs/<org>/summary/)
//   <govkit-feed>       earned-on-tasks rows               (same summary, flattened)
//   <govkit-checklist>  genesis checklist modules + items  (orgs/<org>/checklist/)
//   <govkit-tasks>      open tracker work                  (tasksources/orgs/<org>/tasks/open/)
//   <govkit-money>      project portfolio + totals         (projects/orgs/<org>/portfolio/)
//   <govkit-reachout>   who to reach out to next, from the team CRM  (projects/orgs/<org>/crm/)
//   <govkit-deals>      the team CRM's deals: won, and with an amount (projects/orgs/<org>/crm/)
//                       Both take data-crm (the team CRM origin) to link each row to its lead.
//   <govkit-captable>   the company: founders pool, investors, not yet sold
//   <govkit-offering>   the round on offer, to anyone signed in (an outside investor)
//                                                          (captable/orgs/<org>/)
//   <govkit-onboarding> "Set up" button + the step-at-a-time setup dialog
//                       (accounts/me via window.__gkMe, captable/orgs/<org>/)
//   <govkit-activity>   the org's attention rail           (commons/orgs/<org>/attention/)
//   <govkit-ventures>   venture cards + raise-a-hand       (commons/ventures/; data-up only,
//                       no data-org — it lists every venture for a person in the pool)
//   <govkit-news>       what the ventures did about ME      (commons/news/mine/; data-up only,
//                       no data-org — it is the signed-in person's own rail)
//
// Contract with the host page:
//   - Every component takes data-up (GovKit origin, e.g. https://dash.workers.vc —
//     include the base path if GovKit is deployed under one) and data-org (org slug).
//   - <govkit-feed data-limit="8">; <govkit-tasks data-limit="6"
//     data-tasks-app="https://marten.workers.vc"> — a row OPENS THE TASK in place
//     (title and detail editable, saved to the team's own board); the way out to
//     the board lives inside the sheet and prefers the tasks-app deep link,
//     falling back to the tracker's own URL.
//   - <govkit-checklist data-reading="https://.../curriculum"> — optional. When
//     set, each module's panel links to the reading doc at
//     <data-reading>#chapter-<module-key>. A pointer keyed by module key, never a
//     copy of the text. Omit it and no read link renders.
//   - <govkit-checklist data-week="1"> — optional. Which week of the program today
//     is, from the host's cohort clock. That module opens first instead of the
//     first one with work left. Omit it and nothing changes.
//   - <govkit-checklist data-closed> — optional. No module opens until the person
//     opens one (a module they opened this session stays open).
//   - Every fetch carries credentials: 'include' (the member's own GovKit session;
//     cross-origin needs GovKit's CORS allowlist — see PLAN-cohort-dash.md).
//     GETs are deduped per URL for the life of the page, so components that read
//     the same endpoint share one request. Identity is the host page's business:
//     the page-wide convention is window.__gkMe[<govkit base>] = THE accounts/me
//     promise (created by whoever needs it first — cohort-nav.js or the host
//     page); these components never fetch accounts/me themselves.
//   - Any failure (non-200, network, bad payload) or an empty dataset renders
//     NOTHING and sets `hidden` on the host element: signed-out and non-member
//     visitors just see fewer cards. Never placeholder or demo data.
//   - DOM writes are textContent/attribute only — no innerHTML with data anywhere.
//
// Pie colors read the host page's --s0..--s5 CSS variables (the cohort dash palette,
// ported from workers.vc cohort_dash.html) and fall back to a neutral palette.

(function () {
  'use strict';
  if (window.__govkitEmbedLoaded) return;
  window.__govkitEmbedLoaded = true;

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var FALLBACK_COLORS = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#b07aa1', '#76b7b2'];

  function ensureStyles() {
    if (document.getElementById('govkit-embed-styles')) return;
    var s = document.createElement('style');
    s.id = 'govkit-embed-styles';
    s.textContent = [
      'govkit-pie, govkit-feed, govkit-checklist, govkit-tasks, govkit-money,',
      'govkit-reachout, govkit-deals,',
      'govkit-activity, govkit-ventures, govkit-news, govkit-captable, govkit-offering {',
      '  display: block; font-family: system-ui, -apple-system, sans-serif;',
      '  font-size: 13px; color: inherit; line-height: 1.4;',
      '}',
      'govkit-pie[hidden], govkit-feed[hidden], govkit-checklist[hidden], govkit-tasks[hidden],',
      'govkit-money[hidden], govkit-activity[hidden], govkit-ventures[hidden],',
      'govkit-reachout[hidden], govkit-deals[hidden],',
      'govkit-news[hidden], govkit-captable[hidden], govkit-offering[hidden] { display: none; }',
      'govkit-offering { font-size: 15px; }',
      'govkit-offering .off-sub { font-size: 13px; opacity: 0.7; margin-bottom: 10px; }',
      'govkit-offering .off-terms { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0 0 18px; }',
      'govkit-offering .off-terms dt { opacity: 0.7; }',
      'govkit-offering .off-terms dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }',
      'govkit-offering .off-pie { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; margin-bottom: 14px; }',
      'govkit-offering .off-legend { list-style: none; margin: 0; padding: 0; }',
      'govkit-offering .off-legend li { margin: 4px 0; }',
      'govkit-offering .off-legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }',
      'govkit-offering .off-legend b { font-variant-numeric: tabular-nums; }',
      'govkit-offering .what { opacity: 0.65; }',
      'govkit-offering .off-docs { margin: 0; padding-left: 18px; }',
      'govkit-offering .off-pf { border-collapse: collapse; margin: 0 0 6px; min-width: 260px; }',
      'govkit-offering .off-pf td { padding: 4px 18px 4px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      'govkit-offering .off-pf td.num { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }',
      'govkit-offering .off-pf tr.tot td { border-bottom: none; }',
      'govkit-offering .off-note { font-size: 13px; opacity: 0.7; margin: 0 0 14px; }',
      '@media (max-width: 480px) { govkit-offering .off-terms { grid-template-columns: 1fr; gap: 0 0; } govkit-offering .off-terms dd { margin-bottom: 8px; } }',
      'govkit-offering .off-btn { display: inline-block; padding: 10px 16px; border-radius: 8px; background: #1f2937; color: #fff; text-decoration: none; font-weight: 600; }',
      // The shares read UNDER the pie in a narrow card (golda 2026-08-17): side
      // by side they were squeezed and the names ran over the edge. Beside the
      // pie only when the card itself is wide enough for the names.
      'govkit-pie { container-type: inline-size; }',
      'govkit-pie .piewrap { display: grid; grid-template-columns: 1fr; gap: 14px; justify-items: center; }',
      '@container (min-width: 340px) {',
      '  govkit-pie .piewrap { grid-template-columns: 140px 1fr; gap: 16px; align-items: start; }',
      '  govkit-pie .piewrap svg { width: 140px; height: 140px; }',
      '  govkit-pie .leg-row { padding: 4px 6px; }',
      '  govkit-pie .leg-row .who { overflow: hidden; text-overflow: ellipsis; min-width: 0; }',
      '}',
      '@container (min-width: 480px) {',
      '  govkit-pie .piewrap { grid-template-columns: 170px 1fr; gap: 20px; }',
      '  govkit-pie .piewrap svg { width: 170px; height: 170px; }',
      '}',
      'govkit-pie .pieleg { display: grid; gap: 2px; width: 100%; }',
      'govkit-pie .leg-row[hidden] { display: none; }',
      'govkit-pie .leg-more { justify-self: start; font: inherit; font-size: 12px; color: inherit; opacity: 0.7;',
      '  background: none; border: 0; padding: 4px 10px; cursor: pointer; }',
      'govkit-pie .leg-more:hover { opacity: 1; text-decoration: underline; }',
      'govkit-pie .leg-row { padding: 5px 10px; border-radius: 7px; }',
      'govkit-pie .leg-row .top { display: flex; align-items: center; gap: 8px; }',
      'govkit-pie .leg-row .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }',
      'govkit-pie .leg-row .who { font-size: 13px; white-space: nowrap; }',
      'govkit-pie .leg-row .pct { margin-left: auto; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }',
      'govkit-pie .leg-row .sub { font-size: 12px; opacity: 0.65; margin-left: 18px; font-variant-numeric: tabular-nums; }',
      'govkit-pie .pie-sub { font-size: 12px; opacity: 0.65; margin-bottom: 8px; }',
      'govkit-feed table, govkit-tasks table, govkit-money table { width: 100%; border-collapse: collapse; }',
      'govkit-feed td, govkit-tasks td, govkit-money td {',
      '  padding: 8px 10px; border-bottom: 1px solid rgba(127,127,127,0.2); font-size: 13px; vertical-align: middle;',
      '}',
      'govkit-feed tr:last-child td, govkit-tasks tr:last-child td, govkit-money tr:last-child td { border-bottom: none; }',
      'govkit-feed .who { display: flex; align-items: center; gap: 8px; white-space: nowrap; }',
      'govkit-feed .pdot { width: 8px; height: 8px; border-radius: 50%; flex: none; }',
      // A narrow card: the task on its own line, who and how much under it.
      'govkit-feed { container-type: inline-size; }',
      '@container (max-width: 480px) {',
      '  govkit-feed table, govkit-feed tbody { display: block; }',
      '  govkit-feed tr { display: grid; grid-template-columns: 1fr auto; column-gap: 10px; row-gap: 2px;',
      '    padding: 8px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      '  govkit-feed tr:last-child { border-bottom: none; }',
      '  govkit-feed td { display: block; padding: 0; border: none; }',
      '  govkit-feed td.tk { grid-column: 1 / 3; grid-row: 1; }',
      '  govkit-feed td:first-child { grid-column: 1; grid-row: 2; font-size: 12px; opacity: 0.75; }',
      '  govkit-feed td.val { grid-column: 2; grid-row: 2; font-size: 12.5px; }',
      '}',
      'govkit-reachout ul, govkit-deals ul { list-style: none; margin: 0; padding: 0; }',
      'govkit-reachout li, govkit-deals li { display: flex; gap: 10px; align-items: baseline;',
      '  padding: 7px 0; border-top: 1px solid rgba(127,127,127,0.15); }',
      'govkit-reachout li:first-child, govkit-deals li:first-child { border-top: none; }',
      'govkit-reachout .who, govkit-deals .who { font-weight: 500; color: inherit; text-decoration: none;',
      '  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }',
      'govkit-reachout a.who:hover, govkit-deals a.who:hover { text-decoration: underline; }',
      'govkit-reachout .why, govkit-deals .why { flex: 1; min-width: 0; font-size: 12px; opacity: 0.65;',
      '  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }',
      'govkit-reachout .acts { display: flex; gap: 10px; flex: none; font-size: 12px; }',
      'govkit-deals .num { flex: none; font-variant-numeric: tabular-nums; font-weight: 600; }',
      'govkit-feed .val, govkit-money .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; opacity: 0.85; }',
      // The task sheet. Lives on <body>, so it reads the host page's own tokens
      // (--surface / --ink / --hairline / --font-head) and falls back to warm
      // paper. NOTHING MOVES: no transition, no fade, no shadow — a hairline and
      // a breath of space, per the pattern language.
      '.gk-sheet-backdrop {',
      '  position: fixed; inset: 0; z-index: 9999; background: rgba(23,19,16,0.5);',
      '  display: flex; align-items: center; justify-content: center; padding: 24px;',
      '}',
      '.gk-sheet {',
      '  background: var(--surface, #fffdf7); color: var(--ink, #26221c);',
      '  border: 1px solid var(--hairline, #e6e1d8); border-radius: 10px;',
      '  width: min(880px, 100%); max-height: 100%; overflow: auto;',
      '  font-family: system-ui, -apple-system, sans-serif; font-size: 14px; line-height: 1.5;',
      '}',
      '@media (max-width: 560px) { .gk-sheet-backdrop { padding: 0; } .gk-sheet { border-radius: 0; height: 100%; } }',
      '.gk-sheet-head {',
      '  display: flex; align-items: flex-start; gap: 12px;',
      '  padding: 18px 20px 12px; border-bottom: 1px solid var(--hairline, #e6e1d8);',
      '}',
      '.gk-sheet-title { margin: 0; flex: 1; font-family: var(--font-head, Georgia, serif); font-size: 20px; font-weight: 600; }',
      '.gk-sheet-close {',
      '  flex: none; background: none; border: 1px solid transparent; color: inherit;',
      '  font-size: 20px; line-height: 1; padding: 2px 8px; border-radius: 6px; cursor: pointer;',
      '}',
      '.gk-sheet-close:hover { border-color: var(--hairline, #e6e1d8); }',
      '.gk-sheet-body { padding: 16px 20px 20px; display: grid; gap: 14px; }',
      '.gk-field { display: block; }',
      '.gk-flabel {',
      '  display: block; margin-bottom: 5px; font-size: 11px; font-weight: 700;',
      '  letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted, #736b5c);',
      '}',
      '.gk-field input, .gk-field textarea {',
      '  width: 100%; box-sizing: border-box; font: inherit; color: inherit;',
      '  background: var(--page, #faf9f6); border: 1px solid var(--hairline, #e6e1d8);',
      '  border-radius: 6px; padding: 8px 10px; resize: vertical;',
      '}',
      '.gk-field input:focus, .gk-field textarea:focus { outline: none; border-color: var(--accent, #0f766e); }',
      '.gk-sheet-meta {',
      '  display: flex; flex-wrap: wrap; align-items: center; gap: 14px;',
      '  padding-top: 12px; border-top: 1px solid var(--hairline, #e6e1d8);',
      '  font-size: 13px; color: var(--muted, #736b5c);',
      '}',
      // STATUS IS A WORD, NOT A COLOR.
      '.gk-sheet-status { font-weight: 600; color: var(--ink, #26221c); }',
      '.gk-moverow { display: flex; flex-wrap: wrap; gap: 6px; }',
      '.gk-move {',
      '  font: inherit; font-size: 13px; cursor: pointer; color: inherit;',
      '  background: none; border: 1px solid var(--hairline, #e6e1d8);',
      '  border-radius: 6px; padding: 5px 12px;',
      '}',
      '.gk-move:hover:not(:disabled) { border-color: currentColor; }',
      '.gk-move.on { font-weight: 600; border-color: currentColor; cursor: default; }',
      '.gk-move:disabled:not(.on) { opacity: 0.5; cursor: default; }',
      '.gk-sheet-note { padding: 0 20px 16px; font-size: 13px; color: var(--ink, #26221c); }',
      // What the item is asking for, above the box you answer it in.
      '.gk-sheet-brief { margin: 0; font-size: 14px; line-height: 1.5; opacity: 0.85; }',
      '.gk-sheet-intro { display: grid; gap: 6px; }',
      '.gk-sheet-bullets { margin: 0; padding-left: 18px; font-size: 14px; line-height: 1.5; opacity: 0.85; }',
      '.gk-sheet-bullets li { margin: 2px 0; }',
      'govkit-tasks .rowopen {',
      '  background: none; border: none; padding: 0; font: inherit; color: inherit;',
      '  text-align: left; cursor: pointer; text-decoration: underline;',
      '  text-underline-offset: 2px; text-decoration-thickness: 1px;',
      '}',
      'govkit-checklist .module { margin: 0 0 2px; }',
      'govkit-checklist .module-head {',
      '  display: flex; align-items: center; gap: 8px; width: 100%;',
      '  background: none; border: none; color: inherit; font: inherit;',
      '  padding: 7px 8px; border-radius: 7px; cursor: pointer; text-align: left;',
      '}',
      'govkit-checklist .module-head:hover { background: rgba(127,127,127,0.12); }',
      'govkit-checklist .module-head:focus-visible { outline: 2px solid currentColor; outline-offset: 1px; }',
      // Chevron drawn in CSS, not a glyph: font coverage for the arrow characters
      // is uneven and at this size a missing one degrades to a dot.
      'govkit-checklist .module-head .caret {',
      '  flex: none; width: 0; height: 0; opacity: 0.6;',
      '  border-left: 5px solid currentColor;',
      '  border-top: 4px solid transparent; border-bottom: 4px solid transparent;',
      '  transition: transform 120ms ease;',
      '}',
      'govkit-checklist .module-head[aria-expanded="true"] .caret { transform: rotate(90deg); }',
      'govkit-checklist .module-head .count.alldone { opacity: 1; font-weight: 600; }',
      'govkit-checklist .module-head .title { font-weight: 600; font-size: 13px; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }',
      'govkit-checklist .module-head .count { margin-left: auto; font-size: 12px; opacity: 0.65; font-variant-numeric: tabular-nums; }',
      'govkit-checklist ul { list-style: none; margin: 2px 0 8px; padding: 0 0 0 22px; }',
      'govkit-checklist ul[hidden] { display: none; }',
      'govkit-checklist li { display: flex; gap: 8px; padding: 3px 0; font-size: 13px; align-items: baseline; }',
      'govkit-checklist li .tick { flex: none; width: 1.4em; text-align: center; background: none; border: none; color: inherit; font: inherit; cursor: pointer; padding: 0; border-radius: 4px; }',
      'govkit-checklist li .tick:hover { background: rgba(127,127,127,0.15); }',
      'govkit-checklist li.done { opacity: 0.6; }',
      'govkit-checklist li.done .item-title { text-decoration: line-through; }',
      // The title opens the item. A button, because it does something; styled as
      // the text it replaces so the list still reads as a list.
      // One line per item; the whole text is in the tooltip and in the sheet it opens.
      'govkit-checklist li .item-title { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden;',
      '  text-overflow: ellipsis; text-align: left; background: none;',
      '  border: none; color: inherit; font: inherit; padding: 0; cursor: pointer; border-radius: 4px; }',
      'govkit-checklist li .item-title:hover { text-decoration: underline; }',
      'govkit-checklist li .item-title:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }',
      'govkit-checklist li .haswords { flex: none; font-size: 11px; opacity: 0.65; }',
      'govkit-checklist li.readmore { padding-top: 6px; }',
      'govkit-checklist li.readmore a { font-size: 12.5px; color: inherit; opacity: 0.75; text-decoration: none; }',
      'govkit-checklist li.readmore a:hover { opacity: 1; text-decoration: underline; }',
      'govkit-tasks .status { font-size: 12px; opacity: 0.65; white-space: nowrap; }',
      'govkit-tasks td.grip { width: 1em; padding-right: 0; cursor: grab; opacity: 0.35; user-select: none; }',
      'govkit-tasks tr:hover td.grip { opacity: 0.7; }',
      'govkit-tasks tr.dragging { opacity: 0.4; }',
      'govkit-tasks tr:focus-visible { outline: 2px solid currentColor; outline-offset: -2px; }',
      'govkit-tasks .ordersay { font-size: 12px; padding-top: 6px; }',
      'govkit-tasks .assignee { font-size: 12.5px; opacity: 0.8; white-space: nowrap; }',
      // A narrow card: who and where under the task, not three squeezed columns
      // that push the status past the card's edge.
      'govkit-tasks { container-type: inline-size; }',
      '@container (max-width: 480px) {',
      '  govkit-tasks table, govkit-tasks tbody { display: block; }',
      '  govkit-tasks tr { display: grid; grid-template-columns: 1em 1fr auto; column-gap: 8px; row-gap: 2px;',
      '    padding: 8px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      '  govkit-tasks tr:last-child { border-bottom: none; }',
      '  govkit-tasks td { display: block; padding: 0; border: none; }',
      '  govkit-tasks td.grip { grid-row: span 2; align-self: center; }',
      '  govkit-tasks td:nth-child(2) { grid-column: 2 / 4; }',
      '  govkit-tasks td.assignee { grid-column: 2; font-size: 12px; opacity: 0.65; overflow: hidden; text-overflow: ellipsis; }',
      '  govkit-tasks td.status { grid-column: 3; text-align: right; }',
      '}',
      'govkit-tasks a { color: inherit; }',
      'govkit-money .totals { display: flex; gap: 16px; font-size: 13px; margin-bottom: 8px; flex-wrap: wrap; }',
      'govkit-money .totals .lbl { opacity: 0.65; margin-right: 4px; }',
      'govkit-money .kind { font-size: 12px; opacity: 0.65; white-space: nowrap; }',
      'govkit-money .rowopen { background: none; border: none; padding: 0; font: inherit; font-weight: 600; color: inherit; text-align: left; cursor: pointer; }',
      'govkit-money .rowopen:hover { text-decoration: underline; }',
      'govkit-money tr.split[hidden] { display: none; }',
      'govkit-money tr.split td { padding-top: 0; font-size: 12.5px; }',
      'govkit-money .mline { display: flex; gap: 10px; padding: 2px 0 2px 12px; }',
      'govkit-money .mline .num { margin-left: auto; }',
      // A narrow card: the kind goes under the name so the amounts stay inside.
      'govkit-money { container-type: inline-size; }',
      '@container (max-width: 420px) {',
      '  govkit-money table, govkit-money tbody { display: block; }',
      '  govkit-money tr:not(.split) { display: grid; grid-template-columns: 1fr auto; column-gap: 10px;',
      '    padding: 8px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      '  govkit-money tr.split:not([hidden]) { display: block; }',
      '  govkit-money td { display: block; padding: 0; border: none; }',
      '  govkit-money td.name { grid-column: 1; }',
      '  govkit-money td.num { grid-column: 2; grid-row: 1; }',
      '  govkit-money td.kind { grid-column: 1 / 3; }',
      '}',
      'govkit-activity .row, govkit-news .row { padding: 8px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      'govkit-activity .row:last-child, govkit-news .row:last-child { border-bottom: none; }',
      'govkit-activity .row.answered, govkit-news .row.waiting { opacity: 0.55; }',
      'govkit-activity .head, govkit-news .head { display: flex; align-items: baseline; gap: 8px; }',
      'govkit-activity .who, govkit-news .who { font-weight: 600; font-size: 13px; }',
      'govkit-activity .when, govkit-news .when { margin-left: auto; font-size: 12px; opacity: 0.65; white-space: nowrap; }',
      'govkit-activity .note, govkit-news .note { font-size: 13px; margin: 3px 0 5px; }',
      'govkit-activity .mail, govkit-news .out { font-size: 12.5px; }',
      'govkit-activity .mail a, govkit-news .out a { color: inherit; }',
      'govkit-activity button { font: inherit; font-size: 12.5px; cursor: pointer; border: 1px solid rgba(127,127,127,0.4); background: none; color: inherit; border-radius: 6px; padding: 3px 10px; }',
      'govkit-activity button:hover { background: rgba(127,127,127,0.12); }',
      'govkit-activity .actions { display: flex; gap: 6px; flex-wrap: wrap; }',
      'govkit-activity button.go { border-color: currentColor; font-weight: 600; }',
      'govkit-activity .note.done { opacity: 0.8; }',
      'govkit-ventures .vcard { padding: 10px 0; border-bottom: 1px solid rgba(127,127,127,0.2); }',
      'govkit-ventures .vcard:last-child { border-bottom: none; }',
      'govkit-ventures .vhead { display: flex; align-items: baseline; gap: 8px; }',
      'govkit-ventures .vname { font-weight: 600; font-size: 14px; }',
      'govkit-ventures .vname a { color: inherit; }',
      'govkit-ventures .vsize { margin-left: auto; font-size: 12px; opacity: 0.65; white-space: nowrap; }',
      'govkit-ventures .vpitch { font-size: 13px; margin: 3px 0 6px; }',
      'govkit-ventures .state { font-size: 12.5px; opacity: 0.75; }',
      'govkit-ventures button { font: inherit; font-size: 12.5px; cursor: pointer; border: 1px solid rgba(127,127,127,0.4); background: none; color: inherit; border-radius: 6px; padding: 3px 10px; }',
      'govkit-ventures button:hover { background: rgba(127,127,127,0.12); }',
      // Onboarding: same paper and hairline as the task sheet. Nothing moves.
      'govkit-onboarding[hidden] { display: none; }',
      'govkit-onboarding .gk-ob-open { font: inherit; font-size: 12px; color: inherit; background: none; border: 1px solid var(--hairline, #e6e1d8); border-radius: 999px; padding: 3px 10px; cursor: pointer; }',
      'govkit-onboarding .gk-ob-open:hover { border-color: currentColor; }',
      'dialog.gk-ob { box-sizing: border-box; width: min(520px, calc(100% - 32px)); max-height: calc(100% - 32px); overflow: auto; padding: 18px 20px; border: 1px solid var(--hairline, #e6e1d8); border-radius: 10px; background: var(--surface, #fffdf7); color: var(--ink, #26221c); font-family: system-ui, -apple-system, sans-serif; font-size: 14px; line-height: 1.5; }',
      'dialog.gk-ob::backdrop { background: rgba(23,19,16,0.5); }',
      'dialog.gk-ob a { color: var(--accent, #0f766e); }',
      '.gk-ob-top { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--muted, #736b5c); }',
      '.gk-ob-skip { font: inherit; color: inherit; background: none; border: 0; padding: 4px 0; cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }',
      '.gk-ob-title { margin: 10px 0 8px; font-family: var(--font-head, Georgia, serif); font-size: 20px; font-weight: 600; }',
      '.gk-ob-title:focus { outline: none; }',
      '.gk-ob-body p { margin: 0 0 10px; }',
      '.gk-ob-fact { font-weight: 600; }',
      '.gk-ob-docs { margin: 0 0 8px; padding-left: 18px; }',
      '.gk-ob-row { display: flex; align-items: center; gap: 8px; }',
      '.gk-ob-row input { width: 7em; font: inherit; color: inherit; background: var(--page, #faf9f6); border: 1px solid var(--hairline, #e6e1d8); border-radius: 6px; padding: 6px 8px; }',
      '.gk-ob-pool { margin: 0 0 10px; }',
      '.gk-ob-q { display: flex; flex-direction: column; gap: 4px; margin: 0 0 10px; }',
      '.gk-ob-q select { font: inherit; color: inherit; background: var(--page, #faf9f6); border: 1px solid var(--hairline, #e6e1d8); border-radius: 6px; padding: 6px 8px; max-width: 100%; }',
      '.gk-ob-note { min-height: 1.5em; margin: 0; font-size: 13px; }',
      '.gk-ob-where { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }',
      '.gk-ob-tasks + .gk-ob-links { margin-top: 14px; }',
      '.gk-ob-progress { color: var(--muted, #736b5c); font-size: 13px; }',
      '.gk-ob-group { margin: 14px 0 4px; font-size: 13px; font-weight: 600; color: var(--muted, #736b5c); }',
      '.gk-ob-tasks { list-style: none; margin: 0 0 6px; padding: 0; border-top: 1px solid var(--hairline, #e6e1d8); }',
      '.gk-ob-tasks li { border-bottom: 1px solid var(--hairline, #e6e1d8); }',
      '.gk-ob-task { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; width: 100%; padding: 9px 2px; font: inherit; color: var(--accent, #0f766e); background: none; border: 0; text-align: left; cursor: pointer; }',
      '.gk-ob-task:hover .gk-ob-task-name, .gk-ob-task:focus-visible .gk-ob-task-name { text-decoration: underline; text-underline-offset: 2px; }',
      '.gk-ob-status { color: var(--ink, #26221c); font-size: 13px; text-align: right; }',
      '.gk-ob-status.gk-ob-unset { color: var(--muted, #736b5c); }',
      '.gk-ob-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }',
      '.gk-ob-next { font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; color: var(--surface, #fffdf7); background: var(--accent, #0f766e); border: 1px solid var(--accent, #0f766e); border-radius: 6px; padding: 5px 16px; }',
      'govkit-captable .cap-sub { font-size: 12px; opacity: 0.65; margin-bottom: 8px; }',
      'govkit-captable table { width: 100%; border-collapse: collapse; }',
      'govkit-captable td { padding: 6px 8px; border-bottom: 1px solid rgba(127,127,127,0.2); font-size: 13px; vertical-align: baseline; }',
      'govkit-captable tr:last-child td { border-bottom: none; }',
      'govkit-captable tr.grp td { font-weight: 600; }',
      'govkit-captable tr.in td:first-child { padding-left: 20px; }',
      'govkit-captable tr.in[hidden] { display: none; }',
      'govkit-captable tr.unsold td { opacity: 0.65; }',
      'govkit-captable .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }',
      'govkit-captable .what { font-size: 12px; opacity: 0.65; }',
      'govkit-captable .rowopen { background: none; border: none; padding: 0; font: inherit; font-weight: 600; color: inherit; text-align: left; cursor: pointer; }',
      'govkit-captable .rowopen:hover { text-decoration: underline; }',
      'govkit-ventures textarea { width: 100%; font: inherit; font-size: 13px; margin: 6px 0; border-radius: 6px; border: 1px solid rgba(127,127,127,0.4); background: none; color: inherit; padding: 6px 8px; box-sizing: border-box; }',
    ].join('\n');
    document.head.appendChild(s);
  }

  // --- shared helpers ------------------------------------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function sliceColor(i) {
    var v = getComputedStyle(document.documentElement)
      .getPropertyValue('--s' + (i % 6)).trim();
    return v || FALLBACK_COLORS[i % 6];
  }

  // Only http(s) targets ever become links; anything else renders as plain text.
  function safeHref(url) {
    if (typeof url !== 'string') return null;
    if (/^https?:\/\//i.test(url)) return url;
    return null;
  }

  function num(value) {
    var n = parseFloat(value);
    return isNaN(n) ? null : n;
  }

  // Config for one component: base URL + org slug, or null when unusable.
  function cfg(host) {
    var up = host.dataset.up && host.dataset.up.replace(/\/+$/, '');
    var org = host.dataset.org;
    if (!up || !org) {
      console.warn('[govkit] missing data-up or data-org on', host.tagName.toLowerCase());
      return null;
    }
    return { up: up, org: encodeURIComponent(org) };
  }

  function goDark(host) {
    host.replaceChildren();
    host.hidden = true;
  }

  // GET JSON with the member's own session — ONE request per URL per page.
  // Components asking for the same endpoint (pie + feed both read the org
  // summary) share a single fetch; all fetches still start immediately, in
  // parallel. Throws on ANY non-200 / non-JSON / network problem — callers
  // catch and render nothing (the dash contract). A failure is dropped from
  // the cache so a later mount can retry.
  var inflight = {};
  function jget(url) {
    if (!inflight[url]) {
      inflight[url] = fetch(url, { credentials: 'include' }).then(function (r) {
        if (r.status !== 200) throw new Error('http ' + r.status);
        return r.json();
      });
      inflight[url].catch(function () { delete inflight[url]; });
    }
    return inflight[url];
  }

  // Boilerplate shared by all five components: resolve config, fetch, render;
  // any failure or an empty render() (returns false) hides the host.
  function mount(host, path, render) {
    ensureStyles();
    var c = cfg(host);
    if (!c) return goDark(host);
    jget(c.up + '/api/v1/' + path.replace('{org}', c.org))
      .then(function (data) {
        host.replaceChildren();
        if (render(host, data, c) === false) goDark(host);
        else host.hidden = false;
      })
      .catch(function (err) {
        console.warn('[govkit] ' + host.tagName.toLowerCase() + ':', err.message || err);
        goDark(host);
      });
  }

  // --- <govkit-pie> --------------------------------------------------------
  // Pie SVG + legend. Drawing code ported from workers.vc cohort_dash.html
  // (which this bundle replaces), rebuilt with createElementNS + textContent.

  function renderPie(host, d) {
    var slices = (d.slices || []).filter(function (s) {
      return (num(s.share_pct) || 0) > 0;
    });
    if (!slices.length) return false;

    host.appendChild(el('div', 'pie-sub', d.member_count + ' earning'));

    var wrap = el('div', 'piewrap');
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 200 200');
    svg.setAttribute('width', '200');
    svg.setAttribute('height', '200');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Equity pie by member');

    var C = 100, R = 92, a0 = -Math.PI / 2;
    slices.forEach(function (s, i) {
      var frac = num(s.share_pct) / 100;
      var path;
      if (frac >= 0.9995) {
        // A whole-pie slice: the arc's endpoints coincide and the path
        // collapses to nothing — draw the circle it actually is.
        path = document.createElementNS(SVG_NS, 'circle');
        path.setAttribute('cx', C);
        path.setAttribute('cy', C);
        path.setAttribute('r', R);
      } else {
        var a1 = a0 + frac * 2 * Math.PI;
        var large = (a1 - a0) > Math.PI ? 1 : 0;
        var p = 'M ' + C + ' ' + C +
          ' L ' + (C + R * Math.cos(a0)) + ' ' + (C + R * Math.sin(a0)) +
          ' A ' + R + ' ' + R + ' 0 ' + large + ' 1 ' +
          (C + R * Math.cos(a1)) + ' ' + (C + R * Math.sin(a1)) + ' Z';
        a0 = a1;
        path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', p);
      }
      path.setAttribute('fill', sliceColor(i));
      path.setAttribute('stroke', 'currentColor');
      path.setAttribute('stroke-opacity', '0.15');
      path.setAttribute('stroke-width', '2');
      path.setAttribute('stroke-linejoin', 'round');
      var title = document.createElementNS(SVG_NS, 'title');
      title.textContent = s.member_label + ': ' + num(s.share_pct).toFixed(1) + '%';
      path.appendChild(title);
      svg.appendChild(path);
    });
    wrap.appendChild(svg);

    // The biggest shares, then the rest on request.
    var TOP = 5;
    var leg = el('div', 'pieleg');
    slices.forEach(function (s, i) {
      var row = el('div', 'leg-row');
      row.hidden = i >= TOP;
      var top = el('div', 'top');
      var sw = el('span', 'sw');
      sw.style.background = sliceColor(i);
      top.appendChild(sw);
      top.appendChild(el('span', 'who', s.member_label));
      top.appendChild(el('span', 'pct', num(s.share_pct).toFixed(1) + '%'));
      row.appendChild(top);
      var issued = num(s.issued_total);
      row.appendChild(el('div', 'sub',
        (issued == null ? '' : issued.toLocaleString()) + ' ' + (d.unit_name || '')));
      leg.appendChild(row);
    });
    if (slices.length > TOP) {
      var more = el('button', 'leg-more', (slices.length - TOP) + ' more');
      more.type = 'button';
      more.setAttribute('aria-expanded', 'false');
      more.addEventListener('click', function () {
        var open = more.getAttribute('aria-expanded') !== 'true';
        leg.querySelectorAll('.leg-row').forEach(function (r, i) { r.hidden = !open && i >= TOP; });
        more.setAttribute('aria-expanded', open ? 'true' : 'false');
        more.textContent = open ? 'fewer' : (slices.length - TOP) + ' more';
      });
      leg.appendChild(more);
    }
    wrap.appendChild(leg);
    host.appendChild(wrap);
  }

  // --- <govkit-feed> -------------------------------------------------------
  // Flattened slices -> lines -> tasks: member, task subject, final_value, unit.

  function renderFeed(host, d) {
    var rows = [];
    (d.slices || []).forEach(function (s, i) {
      (s.lines || []).forEach(function (ln) {
        rows.push({
          run: ln.run_id || 0,
          line: ln.line_id || 0,
          who: s.member_label,
          color: i,
          task: (ln.tasks && ln.tasks[0] && ln.tasks[0].subject) || '',
          more: ln.tasks && ln.tasks.length > 1 ? ' +' + (ln.tasks.length - 1) : '',
          value: num(ln.final_value),
        });
      });
    });
    // Newest reviewed work first, whoever did it, not one person's list then the next.
    rows.sort(function (a, b) { return b.run - a.run || b.line - a.line; });
    var limit = parseInt(host.dataset.limit || '8', 10);
    rows = rows.slice(0, limit > 0 ? limit : 8);
    if (!rows.length) return false;

    var table = el('table');
    var tbody = el('tbody');
    rows.forEach(function (r) {
      var tr = el('tr');
      var tdWho = el('td');
      var who = el('span', 'who');
      var dot = el('span', 'pdot');
      dot.style.background = sliceColor(r.color);
      who.appendChild(dot);
      who.appendChild(el('span', 'nm', r.who));
      tdWho.appendChild(who);
      tr.appendChild(tdWho);
      tr.appendChild(el('td', 'tk', r.task + r.more));
      tr.appendChild(el('td', 'val',
        (r.value == null ? '' : '+' + r.value.toLocaleString()) +
        (d.unit_name ? ' ' + d.unit_name : '')));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  // --- <govkit-checklist> --------------------------------------------------

  // The side index is a menu into the curriculum: each module opens and closes.
  // Disclosure pattern, not <details> — a real <button> carrying aria-expanded and
  // aria-controls, which is what screen readers and mobile handle predictably.
  // Which modules are open is pure view state, so it lives in sessionStorage
  // (per org) and nowhere near the server.

  function openModules(org) {
    try {
      return JSON.parse(sessionStorage.getItem('govkit-open:' + org) || '[]');
    } catch (e) { return []; }
  }

  function rememberOpen(org, keys) {
    try { sessionStorage.setItem('govkit-open:' + org, JSON.stringify(keys)); } catch (e) {}
  }

  function renderChecklist(host, d, c) {
    var modules = d.modules || [];
    if (!modules.length) return false;
    var org = (cfg(host) || {}).org || '';
    var tasksApp = host.dataset.tasksApp && host.dataset.tasksApp.replace(/\/+$/, '');
    var open = openModules(org);
    // Nothing opened yet. The host page passes data-week when it knows which week
    // of the program today is (the cohort clock), so the panel opens on the week
    // the team is actually in — an earlier week left unfinished stays closed.
    // Without it, fall back to the first module with work left.
    // data-closed: open nothing until the person opens a module.
    if (!open.length && !('closed' in host.dataset)) {
      var week = parseInt(host.dataset.week, 10);
      var thisWeek = isNaN(week) ? null : modules.filter(function (m) {
        return m.week === week;
      })[0];
      var next = thisWeek || modules.filter(function (m) { return m.done < m.total; })[0];
      if (next) open = [next.key];
    }

    modules.forEach(function (m, i) {
      var box = el('div', 'module');
      // Anchor target for the curriculum's "Open on dashboard" links (#module-<key>).
      box.id = 'module-' + m.key;
      box.setAttribute('data-key', m.key);
      var panelId = 'gkmod-' + (org || 'x') + '-' + m.key;
      var expanded = open.indexOf(m.key) !== -1;

      var head = el('button', 'module-head');
      head.type = 'button';
      head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      head.setAttribute('aria-controls', panelId);
      head.appendChild(el('span', 'caret'));
      head.appendChild(el('span', 'title',
        m.title + (m.week != null ? ' · week ' + m.week : '')));
      head.appendChild(el('span',
        'count' + (m.total && m.done === m.total ? ' alldone' : ''),
        m.done + '/' + m.total));
      box.appendChild(head);

      var ul = el('ul');
      ul.id = panelId;
      ul.hidden = !expanded;
      (m.items || []).forEach(function (item) {
        var li = el('li', item.done ? 'done' : '');
        var btn = el('button', 'tick', item.done ? '✓' : '○');
        btn.type = 'button';
        btn.setAttribute('aria-pressed', item.done ? 'true' : 'false');
        btn.setAttribute('aria-label', (item.done ? 'Uncheck: ' : 'Check: ') + item.title);
        btn.addEventListener('click', function () {
          toggleChecklistItem(host, item.key, li, btn);
        });
        li.appendChild(btn);
        // The title opens the item: what it asks, where to go, and a box for
        // what you did. Retired items are somebody's record of a thing the
        // curriculum no longer asks for, so they stay plain text.
        if (c && !item.retired) {
          var open = el('button', 'item-title', item.title);
          open.type = 'button';
          open.title = item.title;
          var mark = el('span', 'haswords', '✎');
          mark.hidden = !item.has_note;
          mark.title = 'You have written something here';
          open.addEventListener('click', function () {
            openChecklistSheet(c, item, tasksApp, function () { mark.hidden = false; });
          });
          li.appendChild(open);
          li.appendChild(mark);
        } else {
          var plain = el('span', 'item-title', item.title);
          plain.title = item.title;
          li.appendChild(plain);
        }
        ul.appendChild(li);
      });

      // Link TO the reading doc for this module, when the host names one
      // (data-reading = the curriculum doc base). A pointer keyed by module key,
      // never a copy of the text: read there, come back and tick here.
      var readBase = host.dataset.reading && host.dataset.reading.replace(/\/+$/, '');
      if (readBase) {
        var readLi = el('li', 'readmore');
        var a = el('a', null, 'Read the full module →');
        a.href = readBase + '#chapter-' + encodeURIComponent(m.key);
        a.target = '_blank';
        a.rel = 'noopener';
        readLi.appendChild(a);
        ul.appendChild(readLi);
      }

      head.addEventListener('click', function () {
        var nowOpen = ul.hidden;
        ul.hidden = !nowOpen;
        head.setAttribute('aria-expanded', nowOpen ? 'true' : 'false');
        var keys = [];
        var boxes = host.querySelectorAll('.module');
        for (var j = 0; j < boxes.length; j++) {
          var panel = boxes[j].querySelector('ul');
          if (panel && !panel.hidden) keys.push(modules[j].key);
        }
        rememberOpen(org, keys);
      });

      box.appendChild(ul);
      host.appendChild(box);
    });

    // Arrive from the curriculum on #module-<key>: open that module and scroll
    // to it. Modules render after the fetch, so the browser's own anchor jump
    // already missed; apply it here and on later hash changes.
    function focusFromHash() {
      var hm = /^#module-(.+)$/.exec(location.hash || '');
      if (!hm) return;
      var key = decodeURIComponent(hm[1]);
      var boxes = host.querySelectorAll('.module');
      for (var b = 0; b < boxes.length; b++) {
        if (boxes[b].getAttribute('data-key') !== key) continue;
        var panel = boxes[b].querySelector('ul');
        var btn = boxes[b].querySelector('.module-head');
        if (panel && panel.hidden && btn) {
          panel.hidden = false;
          btn.setAttribute('aria-expanded', 'true');
        }
        boxes[b].scrollIntoView({ behavior: 'smooth', block: 'start' });
        break;
      }
    }
    focusFromHash();
    if (!host.__gkHashBound) {
      host.__gkHashBound = true;
      window.addEventListener('hashchange', focusFromHash);
    }
  }

  // The one write the dash makes: work the curriculum in place. The GET above
  // already proved membership (it 403s outsiders), and the endpoint takes the
  // session cookie plus a custom header (preflight-gated, org-allowlisted CORS).
  function toggleChecklistItem(host, itemKey, li, btn) {
    var c = cfg(host);
    if (!c || btn.disabled) return;
    btn.disabled = true;
    fetch(c.up + '/api/v1/orgs/' + c.org + '/checklist/' +
                encodeURIComponent(itemKey) + '/toggle/', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-Govkit-Embed': '1' },
    })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        li.className = d.done ? 'done' : '';
        btn.textContent = d.done ? '✓' : '○';
        btn.setAttribute('aria-pressed', d.done ? 'true' : 'false');
        var count = li.closest('.module') &&
          li.closest('.module').querySelector('.module-head .count');
        if (count && d.module) count.textContent = d.module.done + '/' + d.module.total;
      })
      .catch(function () { /* leave the item as it was */ })
      .then(function () { btn.disabled = false; });
  }

  // --- <govkit-tasks> ------------------------------------------------------
  // Open tracker work. Row links prefer the tasks-app board deep link
  // (<data-tasks-app>/p/<project_slug>/board?story=<ref>), else external_url.

  function renderTasks(host, d, c) {
    var tasks = d.tasks || [];
    var limit = parseInt(host.dataset.limit || '6', 10);
    tasks = tasks.slice(0, limit > 0 ? limit : 6);
    if (!tasks.length) return false;

    var tasksApp = host.dataset.tasksApp && host.dataset.tasksApp.replace(/\/+$/, '');
    var table = el('table');
    var tbody = el('tbody');
    tasks.forEach(function (t) {
      var tr = el('tr');
      tr.dataset.id = t.external_id;
      var tdGrip = el('td', 'grip');
      tdGrip.textContent = '⠿';
      tdGrip.title = 'Drag to arrange';
      tdGrip.setAttribute('aria-hidden', 'true');
      tr.appendChild(tdGrip);
      var tdSubject = el('td');
      // The row opens the task here, in place, rather than throwing the person
      // out to the tracker. The way back out lives inside the sheet.
      if (c && t.external_id != null) {
        var open = el('button', 'rowopen', t.subject || t.external_id);
        open.type = 'button';
        open.addEventListener('click', function () { openTaskSheet(c, t, tasksApp); });
        tdSubject.appendChild(open);
      } else {
        var href = null;
        if (tasksApp && t.project_slug && t.ref != null) {
          href = safeHref(tasksApp + '/p/' + encodeURIComponent(t.project_slug) +
            '/board?story=' + encodeURIComponent(t.ref));
        }
        if (!href && tasksApp) {
          // The tasks app IS the team's front end — never send people to the
          // raw tracker when one is configured (Golda 2026-07-19).
          href = safeHref(t.project_slug
            ? tasksApp + '/p/' + encodeURIComponent(t.project_slug) + '/board'
            : tasksApp + '/tasks');
        }
        if (!href) href = safeHref(t.external_url);
        if (href) {
          var a = el('a', null, t.subject || t.external_id);
          a.setAttribute('href', href);
          a.setAttribute('target', '_blank');
          a.setAttribute('rel', 'noopener');
          tdSubject.appendChild(a);
        } else {
          tdSubject.textContent = t.subject || t.external_id;
        }
      }
      tr.appendChild(tdSubject);
      tr.appendChild(el('td', 'assignee', t.assignee_label || ''));
      tr.appendChild(el('td', 'status', t.status || ''));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
    if (c) makeArrangeable(tbody, host, c);
  }

  // Drag a row up or down and the order is SAVED, on the task, on the tracker
  // (POST tasks/order/ -> the tracker's own order field). Golda 2026-08-17:
  // "must be able save explicit ordering" / "if is explicit respect it" — so
  // the card stops re-sorting these rows by its own rule from then on.
  //
  // HTML5 drag-and-drop with the row as the handle, per the WAI-ARIA APG's
  // advice to keep a keyboard path: Alt+Up / Alt+Down moves the focused row and
  // saves the same way, so arranging the list never needs a mouse.
  function makeArrangeable(tbody, host, c) {
    var dragging = null;

    function rowsInOrder() {
      return Array.prototype.map.call(tbody.querySelectorAll('tr'), function (r) {
        return r.dataset.id;
      });
    }

    function save() {
      var ids = rowsInOrder();
      host.dataset.saving = '1';
      fetch(c.up + '/api/v1/tasksources/orgs/' + c.org + '/tasks/order/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids }),
      })
        .then(function (r) { if (!r.ok) throw new Error(r.status); })
        .catch(function () {
          // The order on screen is not the order on the board. Say so rather
          // than leaving a lie the next reload silently corrects.
          var note = host.querySelector('.ordersay') || el('div', 'ordersay');
          note.textContent = 'That order is not saved. Try again.';
          host.appendChild(note);
        })
        .then(function () {
          delete host.dataset.saving;
          var ok = host.querySelector('.ordersay');
          if (ok && !ok.textContent) ok.remove();
        });
    }

    Array.prototype.forEach.call(tbody.querySelectorAll('tr'), function (tr) {
      tr.setAttribute('draggable', 'true');
      tr.setAttribute('tabindex', '0');
      tr.addEventListener('dragstart', function (e) {
        dragging = tr;
        tr.classList.add('dragging');
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', tr.dataset.id || '');
        }
      });
      tr.addEventListener('dragend', function () {
        tr.classList.remove('dragging');
        dragging = null;
        save();
      });
      tr.addEventListener('dragover', function (e) {
        if (!dragging || dragging === tr) return;
        e.preventDefault();
        var box = tr.getBoundingClientRect();
        var below = (e.clientY - box.top) > box.height / 2;
        tbody.insertBefore(dragging, below ? tr.nextSibling : tr);
      });
      tr.addEventListener('keydown', function (e) {
        if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
        e.preventDefault();
        if (e.key === 'ArrowUp' && tr.previousElementSibling) {
          tbody.insertBefore(tr, tr.previousElementSibling);
        } else if (e.key === 'ArrowDown' && tr.nextElementSibling) {
          tbody.insertBefore(tr.nextElementSibling, tr);
        } else {
          return;
        }
        tr.focus();
        save();
      });
    });
  }

  // --- the task sheet ------------------------------------------------------
  // A task opened over the page at nearly full size, editable where it sits.
  // Mirrors amebo's TaskSheet so a person meets ONE task editor across the
  // cohort: not a cramped strip inside the row, not a small modal. Fields save
  // when you leave them, and only when the text actually changed. Esc closes.
  //
  // The tracker stays the record: every save is a write to the team's own board
  // through GovKit, and nothing is kept here.

  var sheetOpen = null;  // the live sheet, so a second click replaces it

  function fieldRow(label, value, multiline, onSave) {
    var wrap = el('label', 'gk-field');
    wrap.appendChild(el('span', 'gk-flabel', label));
    var input = multiline ? el('textarea') : el('input');
    if (!multiline) input.type = 'text';
    input.value = value || '';
    if (multiline) input.rows = 12;
    input.addEventListener('blur', function () {
      if (input.value === (value || '')) return;
      onSave(input.value, input);
    });
    wrap.appendChild(input);
    return wrap;
  }

  function closeSheet() {
    if (!sheetOpen) return;
    var restore = sheetOpen.restore;
    document.removeEventListener('keydown', sheetOpen.onKey, true);
    sheetOpen.backdrop.remove();
    sheetOpen = null;
    if (restore && restore.focus) restore.focus();
  }

  // The shell every sheet shares: backdrop, heading, body, footer note, Esc and
  // click-outside to close. Callers fill `body` and use `say()` for trouble.
  // ONE editor across the dash, so a task and a checklist item feel like the
  // same thing, because they are.
  function openSheet(label) {
    closeSheet();
    ensureStyles();

    var backdrop = el('div', 'gk-sheet-backdrop');
    var panel = el('div', 'gk-sheet');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', label || 'Task');

    var head = el('div', 'gk-sheet-head');
    // The row's own words paint immediately; the body arrives when it arrives.
    var heading = el('h2', 'gk-sheet-title', label || '');
    head.appendChild(heading);
    var closeBtn = el('button', 'gk-sheet-close', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.addEventListener('click', closeSheet);
    head.appendChild(closeBtn);
    panel.appendChild(head);

    var body = el('div', 'gk-sheet-body');
    panel.appendChild(body);
    var note = el('div', 'gk-sheet-note');
    note.hidden = true;
    panel.appendChild(note);

    backdrop.appendChild(panel);
    backdrop.addEventListener('mousedown', function (e) {
      if (e.target === backdrop) closeSheet();
    });
    document.body.appendChild(backdrop);

    function onKey(e) {
      if (e.key === 'Escape') { e.stopPropagation(); closeSheet(); }
    }
    document.addEventListener('keydown', onKey, true);
    sheetOpen = { backdrop: backdrop, onKey: onKey, restore: document.activeElement };

    return {
      panel: panel,
      body: body,
      heading: heading,
      say: function (message) {
        note.textContent = message;
        note.hidden = !message;
      },
      focusFirst: function () {
        var first = panel.querySelector('input, textarea');
        if (first) first.focus();
      },
    };
  }

  // The way out to the team's board, preferring the tasks app over the raw
  // tracker (Golda 2026-07-19). Null when neither is known yet.
  function boardHref(d, tasksApp) {
    if (tasksApp && d.project_slug && d.ref != null) {
      return safeHref(tasksApp + '/p/' + encodeURIComponent(d.project_slug) +
        '/board?story=' + encodeURIComponent(d.ref));
    }
    return safeHref(d.external_url);
  }

  function openTaskSheet(c, task, tasksApp) {
    var sheet = openSheet(task.subject || 'Task');
    var heading = sheet.heading;
    var body = sheet.body;

    var url = c.up + '/api/v1/tasksources/orgs/' + c.org + '/tasks/' +
      encodeURIComponent(task.external_id) + '/';

    var say = sheet.say;

    function save(fields, input) {
      input.disabled = true;
      say('');
      fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) { render(d); })
        .catch(function () {
          // Keep what the person typed; tell them plainly it is not on the board.
          say('Not saved. Your text is still here — try again, or open it on the board.');
        })
        .then(function () {
          input.disabled = false;
          var row = input.closest && input.closest('.gk-moverow');
          if (row) row.querySelectorAll('button').forEach(function (x) {
            if (!x.classList.contains('on')) x.disabled = false;
          });
        });
    }

    function render(d) {
      heading.textContent = d.subject || task.subject || '';
      body.replaceChildren();

      body.appendChild(fieldRow('Task', d.subject || '', false, function (v, input) {
        save({ subject: v }, input);
      }));
      body.appendChild(fieldRow('Detail', d.description || '', true, function (v, input) {
        save({ description: v }, input);
      }));

      // Where it can go next, in the board's own words. The board names the
      // moves — review, done, archived, whatever this project calls them — so
      // nothing here invents a workflow the team does not have.
      if ((d.statuses || []).length) {
        var moves = el('div', 'gk-moves');
        moves.appendChild(el('span', 'gk-flabel', 'Status'));
        var row = el('div', 'gk-moverow');
        d.statuses.forEach(function (st) {
          var b = el('button', 'gk-move' + (String(st.id) === String(d.status_id) ? ' on' : ''),
                     st.name);
          b.type = 'button';
          if (String(st.id) === String(d.status_id)) {
            b.disabled = true;
          } else {
            b.addEventListener('click', function () {
              row.querySelectorAll('button').forEach(function (x) { x.disabled = true; });
              save({ status_id: st.id }, b);
            });
          }
          row.appendChild(b);
        });
        moves.appendChild(row);
        body.appendChild(moves);
      }

      var meta = el('div', 'gk-sheet-meta');
      if (d.assignee_label) meta.appendChild(el('span', null, d.assignee_label));
      var href = boardHref(d, tasksApp);
      if (href) {
        var a = el('a', null, 'Open on the board');
        a.setAttribute('href', href);
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener');
        meta.appendChild(a);
      }
      if (meta.childNodes.length) body.appendChild(meta);
    }

    // NOT jget: that dedupes per URL for the life of the page, which would serve
    // a stale body to anyone who opened this task before editing it.
    fetch(url, { credentials: 'include', headers: { 'X-Govkit-Embed': '1' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        render(d);
        sheet.focusFirst();
      })
      .catch(function () {
        say('This task could not be opened. It may have been removed from the board.');
      });
  }

  // --- the checklist item sheet --------------------------------------------
  // Same sheet, opened from a curriculum item instead of a task row. What the
  // team types is the answer to the item, and it is saved as a task on their own
  // board, so the work and the record are one thing rather than two.
  //
  // Nothing is created until they save. Open an item, read it, close it, and the
  // board is untouched. Come back next week and the words are still here.

  function openChecklistSheet(c, item, tasksApp, onSaved) {
    var sheet = openSheet(item.title || 'Item');
    var say = sheet.say;
    var body = sheet.body;
    var url = c.up + '/api/v1/tasksources/orgs/' + c.org + '/checklist/' +
      encodeURIComponent(item.key) + '/';

    // What is on the board right now, or null while nothing has been written.
    var current = null;

    function save(text, input) {
      input.disabled = true;
      say('');
      fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: text }),
      })
        .then(function (r) {
          if (r.status === 409) {
            // No board connected yet. The server says where to go; show that
            // rather than a generic failure they cannot act on.
            return r.json().then(function (d) { throw new Error(d.detail || '409'); });
          }
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(function (d) {
          current = d;
          say('Saved to your board.');
          renderFoot();
          if (onSaved) onSaved();
        })
        .catch(function (e) {
          // Keep what the person typed either way; never lose their words.
          say(String(e.message).length > 3
            ? e.message
            : 'Not saved. Your text is still here — try again in a moment.');
        })
        .then(function () { input.disabled = false; });
    }

    var foot = el('div', 'gk-sheet-meta');

    function renderFoot() {
      foot.replaceChildren();
      (item.links || []).forEach(function (link) {
        var href = safeHref(link.url.indexOf('//') === -1 ? c.up + link.url : link.url);
        if (!href) return;
        var a = el('a', null, link.label);
        a.setAttribute('href', href);
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener');
        foot.appendChild(a);
      });
      var board = current && boardHref(current, tasksApp);
      if (board) {
        var b = el('a', null, 'Open on the board');
        b.setAttribute('href', board);
        b.setAttribute('target', '_blank');
        b.setAttribute('rel', 'noopener');
        foot.appendChild(b);
      }
      foot.hidden = !foot.childNodes.length;
    }

    function render(text) {
      body.replaceChildren();
      // Brief and bullets are one block: the sheet body is a grid, so without a
      // wrapper its gap would push an item's bullets away from its own brief.
      if (item.brief || (item.bullets && item.bullets.length)) {
        var intro = el('div', 'gk-sheet-intro');
        if (item.brief) intro.appendChild(el('p', 'gk-sheet-brief', item.brief));
        if (item.bullets && item.bullets.length) {
          var ul = el('ul', 'gk-sheet-bullets');
          item.bullets.forEach(function (line) { ul.appendChild(el('li', null, line)); });
          intro.appendChild(ul);
        }
        body.appendChild(intro);
      }
      body.appendChild(fieldRow('What you did', text || '', true, save));
      renderFoot();
      body.appendChild(foot);
      sheet.focusFirst();
    }

    // 404 here is the ordinary case, not an error: it means nobody has written
    // anything for this item yet.
    fetch(url, { credentials: 'include', headers: { 'X-Govkit-Embed': '1' } })
      .then(function (r) {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function (d) {
        current = d;
        render(d && d.description);
      })
      .catch(function () {
        render('');
        say('Your board could not be reached. Anything you write may not save.');
      });
  }

  // --- <govkit-money> ------------------------------------------------------

  function money(value, currency) {
    var n = num(value);
    if (n == null) return '';
    // Cents only when there are any: 4,500 not 4,500.00.
    var cents = Math.round(n * 100) % 100 !== 0;
    return n.toLocaleString(undefined, { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: 2 }) +
      (currency ? ' ' + currency : '');
  }

  function renderMoney(host, d, c) {
    var projects = d.projects || [];
    if (!projects.length) return false;

    var totals = el('div', 'totals');
    if (d.budget_total != null) {
      var signed = el('span');
      signed.appendChild(el('span', 'lbl', 'signed'));
      signed.appendChild(el('span', 'num', money(d.budget_total, d.currency)));
      totals.appendChild(signed);
    }
    var received = el('span');
    received.appendChild(el('span', 'lbl', 'received'));
    received.appendChild(el('span', 'num', money(d.paid_total, d.currency)));
    totals.appendChild(received);
    host.appendChild(totals);

    var table = el('table');
    var tbody = el('tbody');
    projects.forEach(function (p) {
      var tr = el('tr');
      // The name opens who is promised what on this project and what they were paid.
      var tdName = el('td', 'name');
      var open = el('button', 'rowopen', p.name);
      open.type = 'button';
      open.setAttribute('aria-expanded', 'false');
      tdName.appendChild(open);
      tr.appendChild(tdName);
      tr.appendChild(el('td', 'kind', p.kind + ' · ' + p.status));
      tr.appendChild(el('td', 'num',
        p.budget_total == null
          ? money(p.paid_total, d.currency)
          : money(p.paid_total, null) + ' / ' + money(p.budget_total, d.currency)));
      tbody.appendChild(tr);
      var detail = el('tr', 'split');
      detail.hidden = true;
      var td = el('td');
      td.colSpan = 3;
      detail.appendChild(td);
      tbody.appendChild(detail);
      var loaded = false;
      open.addEventListener('click', function () {
        var show = detail.hidden;
        detail.hidden = !show;
        open.setAttribute('aria-expanded', show ? 'true' : 'false');
        if (!show || loaded || !c) return;
        loaded = true;
        td.textContent = '…';
        jget(c.up + '/api/v1/projects/orgs/' + c.org + '/projects/' + p.id + '/summary/')
          .then(function (sm) {
            td.replaceChildren();
            var members = sm.members || [];
            if (!members.length) { td.textContent = 'No one is promised a share yet.'; return; }
            members.forEach(function (m) {
              var line = el('div', 'mline');
              line.appendChild(el('span', 'mname', m.name));
              line.appendChild(el('span', 'num',
                m.promised == null
                  ? money(m.paid_out, sm.currency) + ' paid'
                  : money(m.paid_out, null) + ' / ' + money(m.promised, sm.currency) + ' · ' + num(m.percent) + '%'));
              td.appendChild(line);
            });
          })
          .catch(function () { loaded = false; td.textContent = 'Could not load.'; });
      });
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  // --- <govkit-activity> ---------------------------------------------------
  // The attention rail: one typed list of everything on this org's plate
  // (commons/orgs/<org>/attention/). Kinds render by shape, not by name, so
  // new kinds work unseen: `done` dims a row; `respond_url` gets the
  // mark-answered POST; a `url` gets a link out.

  function fmtDay(iso) {
    var d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function renderActivity(host, d, c) {
    var rows = d.items || [];
    var limit = parseInt(host.dataset.limit || '8', 10);
    rows = rows.slice(0, limit > 0 ? limit : 8);
    if (!rows.length) return false;

    rows.forEach(function (r) {
      var row = el('div', r.done ? 'row answered' : 'row');
      var head = el('div', 'head');
      head.appendChild(el('span', 'who', r.title || 'Something needs attention'));
      head.appendChild(el('span', 'when', fmtDay(r.since)));
      row.appendChild(head);
      if (r.detail) row.appendChild(el('div', 'note', r.detail));
      if (r.email) {
        var mail = el('div', 'mail');
        var a = el('a', null, r.email);
        a.href = 'mailto:' + r.email;
        mail.appendChild(a);
        row.appendChild(mail);
      }
      var out = safeHref(r.url);
      if (out) {
        var link = el('div', 'mail');
        var la = el('a', null, 'review ↗');
        la.href = out;
        la.target = '_blank';
        la.rel = 'noopener';
        link.appendChild(la);
        row.appendChild(link);
      }
      // The two buttons are two different answers and say so. `accept_url`
      // grants what the row asks for — for a hand-raise, membership, which is
      // the yes. `respond_url` only records that someone replied: it is the no,
      // or a conversation that happened elsewhere. A row that can be granted
      // leads with the grant.
      if ((r.accept_url || r.respond_url) && !r.done) {
        var actions = el('div', 'actions');
        var post = function (btn, url, after) {
          btn.addEventListener('click', function () {
            actions.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
            fetch(c.up + url, {
              method: 'POST',
              credentials: 'include',
              headers: { 'X-Govkit-Embed': '1' },
            })
              .then(function (resp) { if (!resp.ok) throw new Error(resp.status); })
              .then(function () {
                row.className = 'row answered';
                after();
                actions.remove();
              })
              .catch(function () {
                actions.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
              });
          });
          actions.appendChild(btn);
        };
        if (r.accept_url) {
          post(el('button', 'go', r.accept_label || 'Let them in'), r.accept_url, function () {
            // Say what happened, where it happened. The row asked to join and
            // now they have; leaving only a dimmed row would not tell anyone.
            row.appendChild(el('div', 'note done', 'In. They are a member now.'));
          });
        }
        if (r.respond_url) {
          post(el('button', null, 'Mark answered'), r.respond_url, function () {});
        }
        row.appendChild(actions);
      }
      host.appendChild(row);
    });
  }

  // --- <govkit-ventures> ---------------------------------------------------
  // Venture cards for a person in the pool: pitch, size, raise a hand.
  // Not org-scoped: takes data-up only, so it gets its own mount below.

  function ventureButton(card, v, base) {
    var state = el('div');
    card.appendChild(state);

    function showJoined(answered) {
      state.replaceChildren();
      state.appendChild(el('div', 'state', answered
        ? 'You raised a hand — the team has answered.'
        : 'You raised a hand — the team will get back to you.'));
    }

    if (v.is_member) {
      state.appendChild(el('div', 'state', "You're on this team."));
      return;
    }
    if (v.my_interest) {
      showJoined(v.my_interest.answered);
      return;
    }

    var btn = el('button', null, 'I want to join');
    state.appendChild(btn);
    btn.addEventListener('click', function () {
      // First click opens the note box; the send is a second, deliberate click.
      state.replaceChildren();
      var ta = document.createElement('textarea');
      ta.rows = 2;
      ta.placeholder = 'Optional: a line about why, in your own words.';
      var send = el('button', null, 'Send');
      state.appendChild(ta);
      state.appendChild(send);
      send.addEventListener('click', function () {
        send.disabled = true;
        fetch(base + '/api/v1/commons/ventures/' + encodeURIComponent(v.slug) + '/interest/', {
          method: 'POST',
          credentials: 'include',
          headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: ta.value.trim() }),
        })
          .then(function (resp) { if (!resp.ok) throw new Error(resp.status); })
          .then(function () { showJoined(false); })
          .catch(function () { send.disabled = false; });
      });
    });
  }

  function mountVentures(host) {
    ensureStyles();
    var up = host.dataset.up && host.dataset.up.replace(/\/+$/, '');
    if (!up) {
      console.warn('[govkit] missing data-up on govkit-ventures');
      return goDark(host);
    }
    jget(up + '/api/v1/commons/ventures/')
      .then(function (d) {
        host.replaceChildren();
        var ventures = d.ventures || [];
        var limit = parseInt(host.dataset.limit || '0', 10);
        if (limit > 0) ventures = ventures.slice(0, limit);
        if (!ventures.length) return goDark(host);
        ventures.forEach(function (v) {
          var card = el('div', 'vcard');
          var head = el('div', 'vhead');
          var name = el('span', 'vname');
          var site = safeHref(v.website);
          if (site) {
            var a = el('a', null, v.display_name);
            a.href = site;
            a.target = '_blank';
            a.rel = 'noopener';
            name.appendChild(a);
          } else {
            name.textContent = v.display_name;
          }
          head.appendChild(name);
          head.appendChild(el('span', 'vsize',
            v.member_count + ' member' + (v.member_count === 1 ? '' : 's')));
          card.appendChild(head);
          if (v.pitch) card.appendChild(el('div', 'vpitch', v.pitch));
          ventureButton(card, v, up);
          host.appendChild(card);
        });
        host.hidden = false;
      })
      .catch(function (err) {
        console.warn('[govkit] govkit-ventures:', err.message || err);
        goDark(host);
      });
  }

  // --- <govkit-news> -------------------------------------------------------
  // The signed-in person's own rail: a venture answered them, or let them in.
  // Read-only — nothing on it is theirs to action — and person-scoped, so like
  // <govkit-ventures> it takes data-up alone and mounts itself.

  function mountNews(host) {
    ensureStyles();
    var up = host.dataset.up && host.dataset.up.replace(/\/+$/, '');
    if (!up) {
      console.warn('[govkit] missing data-up on govkit-news');
      return goDark(host);
    }
    jget(up + '/api/v1/commons/news/mine/')
      .then(function (d) {
        host.replaceChildren();
        var rows = d.items || [];
        var limit = parseInt(host.dataset.limit || '8', 10);
        rows = rows.slice(0, limit > 0 ? limit : 8);
        if (!rows.length) return goDark(host);
        rows.forEach(function (r) {
          // `done` on this rail means nothing has happened yet: a hand still
          // waiting sits dimmed under the answers.
          var row = el('div', r.done ? 'row waiting' : 'row');
          var head = el('div', 'head');
          head.appendChild(el('span', 'who', r.title || ''));
          head.appendChild(el('span', 'when', fmtDay(r.since)));
          row.appendChild(head);
          if (r.detail) row.appendChild(el('div', 'note', r.detail));
          var out = safeHref(r.url);
          if (out) {
            var wrap = el('div', 'out');
            var a = el('a', null, r.url_label || 'open ↗');
            a.href = out;
            wrap.appendChild(a);
            row.appendChild(wrap);
          }
          host.appendChild(row);
        });
        host.hidden = false;
      })
      .catch(function (err) {
        console.warn('[govkit] govkit-news:', err.message || err);
        goDark(host);
      });
  }

  // --- <govkit-captable> ---------------------------------------------------
  // The whole company in lines that sum to 100%: the founders pool (the pie,
  // scaled into it; opens to the people in it), each investment, and what the
  // investor tranche has not sold yet. Value is at the latest post-money.

  function renderCaptable(host, d) {
    var lines = d.lines || [];
    var pool = lines.filter(function (l) {
      return l.kind === 'founder' || l.kind === 'sponsor' || l.kind === 'pool_unearned';
    });
    var earned = pool.filter(function (l) { return l.kind !== 'pool_unearned'; });
    var investors = lines.filter(function (l) { return l.kind === 'investor'; });
    var unsold = lines.filter(function (l) { return l.kind === 'unsold'; })[0];
    // Founders-pool-only scope: the rest of the company is one line kept elsewhere.
    var outside = lines.filter(function (l) { return l.kind === 'outside'; })[0];
    if (!earned.length && !(d.rounds || []).length) return false;

    var valued = d.latest_post_money != null;
    if (valued) {
      host.appendChild(el('div', 'cap-sub',
        money(d.latest_post_money, d.currency) + ' post-money · ' + (d.latest_round_name || '')));
    }

    function sum(rows) {
      return rows.reduce(function (a, l) { return a + (num(l.value) || 0); }, 0);
    }
    function row(cls, label, what, pct, value) {
      var tr = el('tr', cls);
      var td = el('td');
      if (label instanceof Node) td.appendChild(label); else td.textContent = label;
      if (what) { td.appendChild(document.createTextNode(' ')); td.appendChild(el('span', 'what', what)); }
      tr.appendChild(td);
      tr.appendChild(el('td', 'num', num(pct).toFixed(2) + '%'));
      tr.appendChild(el('td', 'num', valued && value != null ? money(value, null) : ''));
      return tr;
    }

    var table = el('table');
    var tbody = el('tbody');

    var open = el('button', 'rowopen', 'Founders pool');
    open.type = 'button';
    open.setAttribute('aria-expanded', 'false');
    tbody.appendChild(row('grp', earned.length ? open : 'Founders pool',
      earned.length ? earned.length + (earned.length === 1 ? ' holder' : ' holders') : '',
      d.founders_pool_pct, sum(pool)));
    var inRows = earned.map(function (l) {
      var tr = row('in', l.label, l.pie_share_pct != null ? num(l.pie_share_pct).toFixed(1) + '% of pool' : '', l.pct, l.value);
      tr.hidden = true;
      tbody.appendChild(tr);
      return tr;
    });
    open.addEventListener('click', function () {
      var show = open.getAttribute('aria-expanded') !== 'true';
      inRows.forEach(function (r) { r.hidden = !show; });
      open.setAttribute('aria-expanded', show ? 'true' : 'false');
    });

    investors.forEach(function (l) {
      tbody.appendChild(row('', l.label, l.round_name, l.pct, l.value));
    });
    if (unsold) tbody.appendChild(row('unsold', unsold.label, '', unsold.pct, unsold.value));
    if (outside) tbody.appendChild(row('unsold', outside.label, '', outside.pct, outside.value));

    table.appendChild(tbody);
    host.appendChild(table);
  }

  // --- <govkit-onboarding> ------------------------------------------------
  // One step at a time, for the person's role in this org. An admin first sees
  // the list of the five decisions that set up the venture, each with its
  // current answer (GOV.UK "complete multiple tasks" pattern: a task list, each
  // row one link with its status, then one task per page and back to the list);
  // then one step per decision, then the facts. A worker gets the three steps
  // about earning: ownership, options, votes. The steps open in a native modal <dialog> (WAI-ARIA APG
  // dialog pattern: aria-modal, labelled by the step heading, Tab wraps inside,
  // Esc closes, focus returns to the button).
  // Opens by itself once; closing it at any step is remembered in this browser
  // for this person and org, and the button opens it again at that step.
  //
  // Host contract: data-up, data-org, optional data-tasks-app (the board app),
  // data-how (the host's own explainer page). Identity comes from
  // window.__gkMe like every component; no membership, no button.
  //
  // Quoted lines are the team's words from the pages named beside them; lines
  // marked AI-written are new.

  function choiceLabel(list, value) {
    var hit = (list || []).filter(function (o) { return o[0] === value; })[0];
    return hit ? hit[1] : null;
  }

  // A cap table setting's answer: "not set" until anyone saves the settings,
  // even where a default value exists.
  var NOT_SET = { text: 'not set', set: false };
  function settingAnswer(cap, text) {
    if (text == null || !cap.settings_saved) return NOT_SET;
    return { text: text, set: true };
  }

  // Money as the cap table page shows it (captable_fmt.money): $2,000,000.
  var MONEY_SYMBOLS = { USD: '$', EUR: '\u20ac', GBP: '\u00a3' };
  function capMoney(value, currency) {
    var sym = MONEY_SYMBOLS[currency || 'USD'];
    var text = money(value);
    if (!text) return '';
    return sym ? sym + text : text + ' ' + currency;
  }

  function onboardingSteps(lead, ctx) {
    var gk = ctx.up + '/o/' + ctx.org;
    var cap = ctx.cap || {};
    if (lead) {
      var choices = cap.choices || {};
      function setting(name) {
        return function () { return settingAnswer(cap, choiceLabel(choices[name], cap[name])); };
      }
      // Labels are the project owner's; one term per thing, the same on the cap table page.
      return [
        { map: true, title: 'Set up' },
        { decision: true, title: 'Cap table', ask: ['scope'], answer: setting('scope') },
        { decision: true, title: 'Founders pool', pool: true,
          answer: function () {
            var n = num(cap.founders_pool_pct);
            return settingAnswer(cap, n == null ? null : n + '%');
          } },
        { decision: true, title: 'Starting split',
          answer: function () {
            var n = ((ctx.pie || {}).slices || []).filter(function (sl) {
              return num(sl.opening_total) > 0 || (sl.stakes || []).length;
            }).length;
            return n ? { text: n === 1 ? '1 holder' : n + ' holders', set: true } : NOT_SET;
          },
          shown: true,
          links: [[gk + '/members/#starting-shares', 'Starting split']] },
        { decision: true, title: 'Stage', ask: ['stage'], answer: setting('stage') },
        { decision: true, title: 'Valuation', shown: true,
          answer: function () {
            if (cap.latest_post_money == null) return NOT_SET;
            var text = capMoney(cap.latest_post_money, cap.currency) + ' post-money';
            if (cap.latest_round_name) text += ', ' + cap.latest_round_name;
            return { text: text, set: true };
          },
          links: [[gk + '/captable/#rounds', 'Rounds']] },
        { fact: true, title: 'Company', ask: ['incorporated', 'entity_type'],
          answer: function () {
            var t = choiceLabel(choices.entity_type, cap.entity_type);
            return t ? { text: t, set: true } : NOT_SET;
          },
          links: [[gk + '/captable/#stock', 'Stock of record']] },
        { fact: true, title: 'Documents',
          answer: function () {
            var n = ctx.docs ? ctx.docs.length : 0;
            return n ? { text: String(n), set: true } : NOT_SET;
          },
          list: function () { return (ctx.docs || []).map(function (d) { return d.title; }); },
          links: [[gk + '/captable/#documents', 'Documents']] },
      ];
    }
    // One term for what work earns: options (equity model spec, section 3).
    return [
      { title: 'Ownership', text: [
        // the project owner's line, equity model spec
        'Founders earn ownership over time by working.',
      ], links: [[ctx.how, 'How this works']] },
      { title: 'Options', text: [
        // AI-written, from equity model spec section 3
        'Work is valued per task. The team reviews it.',
        'Each drop turns reviewed work into options.',
      ], links: [[ctx.tasksApp ? ctx.tasksApp + '/p/' + ctx.org + '/board' : null, 'Board']] },
      { title: 'Votes', text: [
        // AI-written; apps/orgs/weighting.py weighs votes by earned value
        'Workers vote, weighted by options.',
      ], links: [[gk + '/pie/me/', 'My standing']] },
    ];
  }

  // Per person: the email never sits in the key in the clear.
  function personKey(org, email) {
    var h = 5381;
    for (var i = 0; i < email.length; i++) h = ((h << 5) + h + email.charCodeAt(i)) | 0;
    return 'govkit-onboarding:' + org + ':' + (h >>> 0).toString(36);
  }
  function readSeen(key) {
    try { return JSON.parse(window.localStorage.getItem(key) || 'null'); } catch (e) { return null; }
  }
  function writeSeen(key, step) {
    try { window.localStorage.setItem(key, JSON.stringify({ closed: true, step: step })); } catch (e) { /* private window: it opens again next time */ }
  }

  // A host page's own link, relative or absolute; only http(s) survives.
  function pageHref(v) {
    if (!v) return null;
    try { return safeHref(new URL(v, location.href).href); } catch (e) { return null; }
  }

  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function poolField(c, cap, say) {
    var wrap = el('form', 'gk-ob-pool');
    var input = el('input');
    input.type = 'number';
    input.min = '0';
    input.max = '100';
    input.step = '0.01';
    input.id = 'gk-ob-pool-pct';
    input.setAttribute('aria-labelledby', 'gk-ob-title');
    if (cap.founders_pool_pct != null) input.value = String(num(cap.founders_pool_pct));
    var btn = el('button', 'gk-move', 'Set');
    btn.type = 'submit';
    var row = el('div', 'gk-ob-row');
    row.appendChild(input);
    row.appendChild(el('span', null, '%'));
    row.appendChild(btn);
    wrap.appendChild(row);
    var was = input.value;
    function save(e) {
      if (e) e.preventDefault();
      if (input.value === '' || input.value === was) return;
      btn.disabled = true;
      fetch(c.up + '/api/v1/captable/orgs/' + c.org + '/settings/', {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ founders_pool_pct: input.value }),
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (res) {
        if (!res.ok) throw new Error(res.d && res.d.detail);
        was = input.value;
        cap.founders_pool_pct = res.d.founders_pool_pct;
        cap.settings_saved = true;
        say('Saved.');
      }).catch(function (err) {
        say((err && err.message) || 'Not saved. Try again.');
      }).then(function () { btn.disabled = false; });
    }
    wrap.addEventListener('submit', save);
    input.addEventListener('change', function () { save(); });
    return wrap;
  }

  // One question, one select, saved to the cap table settings when it changes.
  // AI-written labels.
  var SETTING_QUESTIONS = {
    incorporated: { label: 'Incorporated', bool: true,
      options: function () { return [['true', 'Yes'], ['false', 'No']]; },
      value: function (cap) { return cap.incorporated ? 'true' : 'false'; } },
    entity_type: { label: 'Entity type',
      options: function (cap) { return [['', 'not set']].concat((cap.choices || {}).entity_type || []); },
      value: function (cap) { return cap.entity_type || ''; } },
    scope: { options: function (cap) { return (cap.choices || {}).scope || []; },
      value: function (cap) { return cap.scope || ''; } },
    stage: { options: function (cap) { return (cap.choices || {}).stage || []; },
      value: function (cap) { return cap.stage || ''; } },
  };

  function settingFields(c, cap, names, say) {
    var wrap = el('div', 'gk-ob-company');
    names.forEach(function (name) {
      var q = SETTING_QUESTIONS[name];
      var opts = q.options(cap);
      if (!opts.length) return;
      var id = 'gk-ob-' + name;
      var sel = el('select');
      sel.id = id;
      // A question without its own label is the step's one control: the heading names it.
      var label = null;
      if (q.label) {
        label = el('label', 'gk-flabel', q.label);
        label.htmlFor = id;
      } else {
        sel.setAttribute('aria-labelledby', 'gk-ob-title');
      }
      opts.forEach(function (o) {
        var opt = el('option', null, o[1]);
        opt.value = o[0];
        sel.appendChild(opt);
      });
      sel.value = q.value(cap);
      var was = sel.value;
      sel.addEventListener('change', function () {
        var body = {};
        body[name] = q.bool ? sel.value === 'true' : sel.value;
        sel.disabled = true;
        fetch(c.up + '/api/v1/captable/orgs/' + c.org + '/settings/', {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'X-Govkit-Embed': '1', 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }).then(function (r) {
          return r.json().then(function (d) { return { ok: r.ok, d: d }; });
        }).then(function (res) {
          if (!res.ok) throw new Error(res.d && res.d.detail);
          was = sel.value;
          cap[name] = res.d[name];
          cap.settings_saved = true;
          say('Saved.');
        }).catch(function (err) {
          sel.value = was;
          say((err && err.message) || 'Not saved. Try again.');
        }).then(function () { sel.disabled = false; });
      });
      var row = el('div', 'gk-ob-q');
      if (label) row.appendChild(label);
      row.appendChild(sel);
      wrap.appendChild(row);
    });
    return wrap;
  }

  function mountOnboarding(host) {
    ensureStyles();
    host.hidden = true;
    var c = cfg(host);
    var store = window.__gkMe || {};
    var me = c && store[c.up];
    if (!me || !me.then) return;
    var orgSlug = host.dataset.org;
    me.then(function (who) {
      var mine = null;
      (who.memberships || []).forEach(function (m) { if (m.org_slug === orgSlug) mine = m; });
      if (!mine || !who.email) return;
      var lead = mine.role === 'admin';
      if (!lead && ['mentor', 'funder', 'supporter'].indexOf(mine.audience) !== -1) return;
      var ctx = {
        up: c.up, org: c.org, orgName: mine.org_name || orgSlug,
        tasksApp: safeHref((host.dataset.tasksApp || '').replace(/\/+$/, '')),
        how: pageHref(host.dataset.how),
        cap: null,
        pie: null,
        docs: null,
      };
      var key = personKey(orgSlug, who.email);
      var opener = el('button', 'gk-ob-open', 'Set up');
      opener.type = 'button';
      opener.setAttribute('aria-haspopup', 'dialog');
      host.replaceChildren(opener);
      host.hidden = false;

      var dlg = null;
      var at = 0;
      function close(done) {
        if (!dlg) return;
        writeSeen(key, done === true ? 0 : at);
        var d = dlg;
        dlg = null;
        d.close();
        d.remove();
        opener.focus();
      }
      function open(step) {
        if (dlg) return;
        var steps = onboardingSteps(lead, ctx);
        at = Math.min(Math.max(step || 0, 0), steps.length - 1);
        dlg = el('dialog', 'gk-ob');
        dlg.setAttribute('aria-modal', 'true');
        dlg.setAttribute('aria-labelledby', 'gk-ob-title');
        var top = el('div', 'gk-ob-top');
        var where = el('span', 'gk-ob-where');
        var toList = el('button', 'gk-ob-skip gk-ob-tolist', 'Set up');
        toList.type = 'button';
        toList.addEventListener('click', function () { show(0); });
        var count = el('span', 'gk-ob-count');
        where.appendChild(toList);
        where.appendChild(count);
        var skip = el('button', 'gk-ob-skip', 'Close');
        skip.type = 'button';
        skip.addEventListener('click', function () { close(); });
        top.appendChild(where);
        top.appendChild(skip);
        var heading = el('h2', 'gk-ob-title');
        heading.id = 'gk-ob-title';
        heading.tabIndex = -1;
        var body = el('div', 'gk-ob-body');
        var note = el('p', 'gk-ob-note');
        note.setAttribute('role', 'status');
        var foot = el('div', 'gk-ob-foot');
        var back = el('button', 'gk-move', 'Back');
        back.type = 'button';
        var next = el('button', 'gk-ob-next');
        next.type = 'button';
        foot.appendChild(back);
        foot.appendChild(next);
        [top, heading, body, note, foot].forEach(function (n) { dlg.appendChild(n); });

        var decisions = steps.filter(function (s) { return s.decision; });
        var facts = steps.filter(function (s) { return s.fact; });
        function say(msg) { note.textContent = msg || ''; }
        // The list of decisions: each row is one button to its step, with its answer.
        function taskList(group) {
          var ol = el('ol', 'gk-ob-tasks');
          group.forEach(function (s) {
            var li = el('li');
            var b = el('button', 'gk-ob-task');
            b.type = 'button';
            b.appendChild(el('span', 'gk-ob-task-name', s.title));
            if (s.answer) {
              var a = s.answer();
              b.appendChild(el('span', 'gk-ob-status' + (a.set ? '' : ' gk-ob-unset'), a.text));
            }
            b.addEventListener('click', function () { show(steps.indexOf(s)); });
            li.appendChild(b);
            ol.appendChild(li);
          });
          return ol;
        }
        function show(i) {
          at = i;
          var s = steps[i];
          // Decisions are counted against decisions; the map and the facts carry no count.
          if (s.decision) count.textContent = (decisions.indexOf(s) + 1) + ' of ' + decisions.length;
          else if (lead) count.textContent = '';
          else count.textContent = (i + 1) + ' of ' + steps.length;
          toList.hidden = !lead || !!s.map;
          heading.textContent = s.title;
          say('');
          body.replaceChildren();
          (s.text || []).forEach(function (t) { if (t) body.appendChild(el('p', null, t)); });
          var allSet = decisions.every(function (d) { return d.answer().set; });
          if (s.map) {
            var done = decisions.filter(function (d) { return d.answer().set; }).length;
            body.appendChild(el('p', 'gk-ob-progress', done + ' of ' + decisions.length + ' set'));
            body.appendChild(taskList(decisions));
            body.appendChild(el('h3', 'gk-ob-group', 'Facts'));
            body.appendChild(taskList(facts));
          }
          if (s.shown) body.appendChild(el('p', 'gk-ob-fact', s.answer().text));
          if (s.list) {
            var titles = s.list();
            if (titles.length) {
              var ul = el('ul', 'gk-ob-docs');
              titles.forEach(function (t) { ul.appendChild(el('li', null, t)); });
              body.appendChild(ul);
            } else {
              body.appendChild(el('p', 'gk-ob-fact', NOT_SET.text));
            }
          }
          if (s.pool && ctx.cap) body.appendChild(poolField(c, ctx.cap, say));
          if (s.ask && ctx.cap) body.appendChild(settingFields(c, ctx.cap, s.ask, say));
          var links = (s.links || []).filter(function (l) { return l[0]; });
          if (links.length) {
            var p = el('p', 'gk-ob-links');
            links.forEach(function (l, j) {
              if (j) p.appendChild(document.createTextNode(' · '));
              var a = el('a', null, l[1]);
              a.href = l[0];
              p.appendChild(a);
            });
            body.appendChild(p);
          }
          back.hidden = i === 0;
          var last = i === steps.length - 1 || (s.map && allSet);
          next.textContent = s.map && !allSet ? 'Start' : last ? 'Done' : 'Next';
          heading.focus();
        }
        back.addEventListener('click', function () { show(at - 1); });
        next.addEventListener('click', function () {
          var s = steps[at];
          var finished = at === steps.length - 1 ||
            (s.map && decisions.every(function (d) { return d.answer().set; }));
          if (finished) close(true); else show(at + 1);
        });
        dlg.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
        dlg.addEventListener('keydown', function (e) {
          if (e.key === 'Escape') { e.preventDefault(); close(); return; }
          if (e.key !== 'Tab') return;
          var f = Array.prototype.filter.call(dlg.querySelectorAll(FOCUSABLE), function (n) {
            return !n.hidden && n.offsetParent !== null;
          });
          if (!f.length) return;
          var first = f[0], last = f[f.length - 1];
          if (e.shiftKey && (document.activeElement === first || document.activeElement === heading)) {
            e.preventDefault(); last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
          }
        });
        document.body.appendChild(dlg);
        if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
        show(at);
      }

      opener.addEventListener('click', function () {
        var seen = readSeen(key);
        open(seen ? seen.step : 0);
      });

      Promise.all([
        jget(c.up + '/api/v1/captable/orgs/' + c.org + '/')
          .then(function (d) { ctx.cap = d; }, function () { ctx.cap = null; }),
        // The starting split's and the documents' answers; only the lead's list shows them.
        lead ? jget(c.up + '/api/v1/pie/orgs/' + c.org + '/summary/')
          .then(function (d) { ctx.pie = d; }, function () { ctx.pie = null; }) : null,
        lead ? jget(c.up + '/api/v1/captable/orgs/' + c.org + '/documents/')
          .then(function (d) { ctx.docs = d.documents; }, function () { ctx.docs = null; }) : null,
      ]).then(function () { if (!readSeen(key)) open(0); });
    }).catch(function () {});
  }

  // --- <govkit-offering> ---------------------------------------------------
  // The round on offer, for an outside investor. Anyone signed in may read it;
  // signed out, the card offers the sign-in that brings them back here.

  function pctText(v, digits) {
    var n = num(v);
    return n == null ? '' : n.toFixed(digits == null ? 1 : digits).replace(/\.0+$/, '') + '%';
  }

  function dollars(v) {
    var n = num(v);
    return n == null ? '' : '$' + money(n, null);
  }

  function renderOfferingPie(sides) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 200 200');
    svg.setAttribute('width', '180');
    svg.setAttribute('height', '180');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', sides.map(function (s) { return s.label + ' ' + pctText(s.pct); }).join(', '));
    var C = 100, R = 90, a0 = -Math.PI / 2;
    sides.forEach(function (s) {
      var frac = (num(s.pct) || 0) / 100;
      if (frac <= 0) return;
      var a1 = a0 + frac * 2 * Math.PI;
      var p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', 'M' + C + ',' + C +
        ' L' + (C + R * Math.cos(a0)) + ',' + (C + R * Math.sin(a0)) +
        ' A' + R + ',' + R + ' 0 ' + (frac > 0.5 ? 1 : 0) + ' 1 ' +
        (C + R * Math.cos(a1)) + ',' + (C + R * Math.sin(a1)) + ' Z');
      p.setAttribute('fill', s.color);
      p.setAttribute('stroke', '#fff');
      p.setAttribute('stroke-width', '2');
      svg.appendChild(p);
      a0 = a1;
    });
    return svg;
  }

  function renderOffering(host, d) {
    host.appendChild(el('div', 'off-sub', [d.stage, d.instrument_label].filter(Boolean).join(' · ')));

    var terms = el('dl', 'off-terms');
    function term(k, v) {
      if (!v) return;
      terms.appendChild(el('dt', null, k));
      terms.appendChild(el('dd', null, v));
    }
    var post = d.conversion_timing === 'PRE_MONEY' ? 'Pre-money valuation cap' : 'Post-money valuation cap';
    term('Company', d.legal_name || (d.incorporated ? '' : 'Not yet incorporated'));
    term('Raising', dollars(d.target_amount));
    term(post, dollars(d.conversion_valuation_cap || d.post_money));
    term('Pre-money', d.conversion_timing === 'PRE_MONEY' ? '' : dollars(d.pre_money));
    if (d.target_amount && d.target_pct) term(dollars(d.target_amount) + ' buys', pctText(d.target_pct) + ' of the company');
    term('Discount', d.conversion_discount != null ? pctText(num(d.conversion_discount) * 100) : 'None');
    term('Most favored nation', d.conversion_mfn ? 'Yes' : 'No');
    term('Pro rata right', d.pro_rata ? 'Yes' : 'No');
    host.appendChild(terms);

    var pool = num(d.founders_pool_pct) || 0;
    var side = num(d.investor_side_pct) || 0;
    var round = Math.min(num(d.target_pct) || 0, side);
    var sides = [
      { label: 'Founders pool', what: 'earned by work', pct: pool, color: sliceColor(0) },
      { label: 'This round', what: d.target_amount ? dollars(d.target_amount) : '', pct: round, color: sliceColor(1) },
      { label: 'Investor side, not yet sold', what: 'held by the founders until sold', pct: side - round, color: 'rgba(127,127,127,0.35)' }
    ];
    var wrap = el('div', 'off-pie');
    wrap.appendChild(renderOfferingPie(sides));
    var legend = el('ul', 'off-legend');
    sides.forEach(function (s) {
      if (s.pct <= 0) return;
      var li = el('li');
      var dot = el('span', 'dot');
      dot.style.background = s.color;
      li.appendChild(dot);
      li.appendChild(el('b', null, pctText(s.pct)));
      li.appendChild(document.createTextNode(' ' + s.label));
      if (s.what) li.appendChild(el('span', 'what', ' · ' + s.what));
      legend.appendChild(li);
    });
    wrap.appendChild(legend);
    host.appendChild(wrap);

    // The SAFE first: it is the agreement; the rest go with it.
    var pf = d.pro_forma || [];
    if (pf.length) {
      host.appendChild(el('div', 'off-sub', 'Pro forma after this round'));
      var t = el('table', 'off-pf');
      pf.forEach(function (h) {
        var tr = el('tr');
        tr.appendChild(el('td', null, h.label));
        tr.appendChild(el('td', 'num', pctText(h.pct_after)));
        t.appendChild(tr);
      });
      var tr2 = el('tr', 'tot');
      tr2.appendChild(el('td', null, 'This round'));
      tr2.appendChild(el('td', 'num', pctText(d.target_pct)));
      t.appendChild(tr2);
      host.appendChild(t);
      host.appendChild(el('p', 'off-note', 'Founders hold the founders pool and the unsold investor side together, split by their share of the pie.'));
    }

    var docs = (d.documents || []).filter(function (x) { return safeHref(x.url); })
      .sort(function (a, b) { return (b.kind === 'SAFE') - (a.kind === 'SAFE'); });
    if (docs.length) {
      var ul = el('ul', 'off-docs');
      docs.forEach(function (x) {
        var li = el('li');
        var a = el('a', null, x.title || x.kind_label);
        a.href = safeHref(x.url);
        a.target = '_blank';
        a.rel = 'noopener';
        li.appendChild(a);
        ul.appendChild(li);
      });
      host.appendChild(ul);
    }
  }

  function mountOffering(host) {
    ensureStyles();
    var c = cfg(host);
    if (!c) return goDark(host);
    fetch(c.up + '/api/v1/captable/orgs/' + c.org + '/offering/', { credentials: 'include' })
      .then(function (r) {
        host.replaceChildren();
        host.hidden = false;
        if (r.status === 401) {
          host.appendChild(el('p', 'off-gate', 'The terms are for signed-in investors.'));
          var a = el('a', 'off-btn', 'Sign in to see the round');
          a.href = c.up + '/accounts/linkedtrust/start/?next=' + encodeURIComponent(location.href);
          host.appendChild(a);
          return;
        }
        if (r.status === 404) {
          host.appendChild(el('p', 'off-gate', 'No round is open.'));
          return;
        }
        if (r.status !== 200) throw new Error('http ' + r.status);
        return r.json().then(function (d) { renderOffering(host, d); });
      })
      .catch(function (err) {
        console.warn('[govkit] govkit-offering:', err.message || err);
        goDark(host);
      });
  }


  // --- <govkit-reachout> / <govkit-deals> ----------------------------------
  // The team CRM read by GovKit, so the cards fill for anyone signed in to
  // workers.vc whether or not they are signed in to the CRM itself.

  function crmLeadLink(host, id, text) {
    var crm = (host.dataset.crm || '').replace(/\/+$/, '');
    var href = /^https:\/\//.test(crm) ? crm + '/web#id=' + id + '&model=crm.lead&view_type=form' : null;
    var a = el(href ? 'a' : 'span', 'who', text);
    if (href) { a.href = href; a.target = '_blank'; a.rel = 'noopener'; }
    return a;
  }

  function renderReachout(host, d) {
    var leads = d.reachout || [];
    if (!leads.length) return false;
    var ul = el('ul');
    leads.forEach(function (l) {
      var li = el('li');
      var who = crmLeadLink(host, l.id, l.partner_name || l.name);
      if (l.name && l.name !== who.textContent) who.title = l.name;
      li.appendChild(who);
      var why = l.outreach_score_reason || '';
      if (l.last_outreach_date) {
        var t = Date.parse(l.last_outreach_date);
        if (!isNaN(t)) {
          var days = Math.floor(Math.max(0, Date.now() - t) / 86400000);
          why = days === 0 ? 'contacted today' : 'no reply in ' + days + (days === 1 ? ' day' : ' days');
        }
      }
      li.appendChild(el('span', 'why', why));
      var acts = el('span', 'acts');
      if (l.email) {
        var m = el('a', null, 'email');
        m.href = 'mailto:' + l.email;
        acts.appendChild(m);
      }
      if (l.linkedin && /^https?:\/\//i.test(l.linkedin)) {
        var ln = el('a', null, 'LinkedIn');
        ln.href = l.linkedin; ln.target = '_blank'; ln.rel = 'noopener';
        acts.appendChild(ln);
      }
      li.appendChild(acts);
      ul.appendChild(li);
    });
    host.appendChild(ul);
    if (typeof d.count_to_reach === 'number') host.setAttribute('data-count', String(d.count_to_reach));
    return true;
  }

  function dealAmount(deal, currency) {
    var sym = currency === 'USD' ? '$' : '';
    var tail = sym ? '' : (currency ? ' ' + currency : '');
    if (deal.recurring_revenue != null) {
      var per = deal.recurring_months === 1 ? '/mo' : deal.recurring_months === 12 ? '/yr'
        : (deal.recurring_plan ? ' ' + deal.recurring_plan.toLowerCase() : '');
      return sym + money(deal.recurring_revenue, null) + tail + per;
    }
    if (deal.expected_revenue != null && num(deal.expected_revenue) > 0) {
      return sym + money(deal.expected_revenue, null) + tail;
    }
    return '';
  }

  function renderDeals(host, d) {
    var deals = d.deals || [];
    if (!deals.length) return false;
    var ul = el('ul');
    deals.forEach(function (deal) {
      var li = el('li');
      var who = crmLeadLink(host, deal.id, deal.partner_name || deal.name);
      if (deal.name && deal.name !== who.textContent) who.title = deal.name;
      li.appendChild(who);
      li.appendChild(el('span', 'why', deal.won ? deal.stage || 'Won' : (deal.stage || '') +
        (deal.probability != null ? ' · ' + Math.round(deal.probability) + '%' : '')));
      li.appendChild(el('span', 'num', dealAmount(deal, d.currency)));
      ul.appendChild(li);
    });
    host.appendChild(ul);
    return true;
  }

  // --- element registration ------------------------------------------------

  function define(tag, path, render) {
    if (customElements.get(tag)) return;
    customElements.define(tag, class extends HTMLElement {
      // Once per element. The dashboard grid reorders cards in the page when a
      // card changes height, which connects each component again; mounting
      // again refetched and redrew it, throwing away what the person had open.
      connectedCallback() { if (this.__mounted) return; this.__mounted = true; mount(this, path, render); }
    });
  }

  define('govkit-pie', 'pie/orgs/{org}/summary/', renderPie);
  define('govkit-feed', 'pie/orgs/{org}/summary/', renderFeed);
  define('govkit-checklist', 'orgs/{org}/checklist/', renderChecklist);
  define('govkit-tasks', 'tasksources/orgs/{org}/tasks/open/', renderTasks);
  define('govkit-money', 'projects/orgs/{org}/portfolio/', renderMoney);
  define('govkit-reachout', 'projects/orgs/{org}/crm/', renderReachout);
  define('govkit-deals', 'projects/orgs/{org}/crm/', renderDeals);
  define('govkit-captable', 'captable/orgs/{org}/', renderCaptable);
  define('govkit-activity', 'commons/orgs/{org}/attention/', renderActivity);
  if (!customElements.get('govkit-ventures')) {
    customElements.define('govkit-ventures', class extends HTMLElement {
      connectedCallback() { if (this.__mounted) return; this.__mounted = true; mountVentures(this); }
    });
  }
  if (!customElements.get('govkit-onboarding')) {
    customElements.define('govkit-onboarding', class extends HTMLElement {
      connectedCallback() { if (this.__mounted) return; this.__mounted = true; mountOnboarding(this); }
    });
  }
  if (!customElements.get('govkit-offering')) {
    customElements.define('govkit-offering', class extends HTMLElement {
      connectedCallback() { if (this.__mounted) return; this.__mounted = true; mountOffering(this); }
    });
  }
  if (!customElements.get('govkit-news')) {
    customElements.define('govkit-news', class extends HTMLElement {
      connectedCallback() { if (this.__mounted) return; this.__mounted = true; mountNews(this); }
    });
  }
})();
