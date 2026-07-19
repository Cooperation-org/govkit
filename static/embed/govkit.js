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
//
// Contract with the host page:
//   - Every component takes data-up (GovKit origin, e.g. https://dash.workers.vc —
//     include the base path if GovKit is deployed under one) and data-org (org slug).
//   - <govkit-feed data-limit="8">; <govkit-tasks data-limit="6"
//     data-tasks-app="https://martin.workers.vc"> (row links prefer the tasks-app
//     board deep link, falling back to the tracker's own URL).
//   - Every fetch carries credentials: 'include' (the member's own GovKit session;
//     cross-origin needs GovKit's CORS allowlist — see PLAN-cohort-dash.md).
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
      'govkit-pie, govkit-feed, govkit-checklist, govkit-tasks, govkit-money {',
      '  display: block; font-family: system-ui, -apple-system, sans-serif;',
      '  font-size: 14px; color: inherit; line-height: 1.4;',
      '}',
      'govkit-pie .piewrap { display: grid; grid-template-columns: 200px 1fr; gap: 18px; align-items: center; }',
      '@media (max-width: 560px) { govkit-pie .piewrap { grid-template-columns: 1fr; justify-items: center; } }',
      'govkit-pie .pieleg { display: grid; gap: 2px; width: 100%; }',
      'govkit-pie .leg-row { padding: 5px 10px; border-radius: 7px; }',
      'govkit-pie .leg-row .top { display: flex; align-items: center; gap: 8px; }',
      'govkit-pie .leg-row .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }',
      'govkit-pie .leg-row .who { font-size: 13.5px; white-space: nowrap; }',
      'govkit-pie .leg-row .pct { margin-left: auto; font-size: 13.5px; font-weight: 600; font-variant-numeric: tabular-nums; }',
      'govkit-pie .leg-row .sub { font-size: 12px; opacity: 0.65; margin-left: 18px; font-variant-numeric: tabular-nums; }',
      'govkit-pie .pie-sub { font-size: 12px; opacity: 0.65; margin-bottom: 8px; }',
      'govkit-feed table, govkit-tasks table, govkit-money table { width: 100%; border-collapse: collapse; }',
      'govkit-feed td, govkit-tasks td, govkit-money td {',
      '  padding: 8px 10px; border-bottom: 1px solid rgba(127,127,127,0.2); font-size: 13.5px; vertical-align: middle;',
      '}',
      'govkit-feed tr:last-child td, govkit-tasks tr:last-child td, govkit-money tr:last-child td { border-bottom: none; }',
      'govkit-feed .who { display: flex; align-items: center; gap: 8px; white-space: nowrap; }',
      'govkit-feed .pdot { width: 8px; height: 8px; border-radius: 50%; flex: none; }',
      'govkit-feed .val, govkit-money .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; opacity: 0.85; }',
      'govkit-checklist .module { margin: 0 0 10px; }',
      'govkit-checklist .module-head { display: flex; align-items: baseline; gap: 8px; }',
      'govkit-checklist .module-head .title { font-weight: 600; font-size: 13.5px; }',
      'govkit-checklist .module-head .count { margin-left: auto; font-size: 12px; opacity: 0.65; font-variant-numeric: tabular-nums; }',
      'govkit-checklist ul { list-style: none; margin: 4px 0 0; padding: 0; }',
      'govkit-checklist li { display: flex; gap: 8px; padding: 3px 0; font-size: 13px; align-items: baseline; }',
      'govkit-checklist li .tick { flex: none; width: 1.1em; text-align: center; }',
      'govkit-checklist li.done { opacity: 0.6; }',
      'govkit-checklist li.done .item-title { text-decoration: line-through; }',
      'govkit-tasks .status { font-size: 12px; opacity: 0.65; white-space: nowrap; }',
      'govkit-tasks .assignee { font-size: 12.5px; opacity: 0.8; white-space: nowrap; }',
      'govkit-tasks a { color: inherit; }',
      'govkit-money .totals { display: flex; gap: 16px; font-size: 13px; margin-bottom: 8px; flex-wrap: wrap; }',
      'govkit-money .totals .lbl { opacity: 0.65; margin-right: 4px; }',
      'govkit-money .kind { font-size: 12px; opacity: 0.65; white-space: nowrap; }',
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

  // GET JSON with the member's own session. Throws on ANY non-200 / non-JSON /
  // network problem — callers catch and render nothing (the dash contract).
  function jget(url) {
    return fetch(url, { credentials: 'include' }).then(function (r) {
      if (r.status !== 200) throw new Error('http ' + r.status);
      return r.json();
    });
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
      var a1 = a0 + frac * 2 * Math.PI;
      var large = (a1 - a0) > Math.PI ? 1 : 0;
      var p = 'M ' + C + ' ' + C +
        ' L ' + (C + R * Math.cos(a0)) + ' ' + (C + R * Math.sin(a0)) +
        ' A ' + R + ' ' + R + ' 0 ' + large + ' 1 ' +
        (C + R * Math.cos(a1)) + ' ' + (C + R * Math.sin(a1)) + ' Z';
      a0 = a1;
      var path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', p);
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

    var leg = el('div', 'pieleg');
    slices.forEach(function (s, i) {
      var row = el('div', 'leg-row');
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
          who: s.member_label,
          color: i,
          task: (ln.tasks && ln.tasks[0] && ln.tasks[0].subject) || '',
          more: ln.tasks && ln.tasks.length > 1 ? ' +' + (ln.tasks.length - 1) : '',
          value: num(ln.final_value),
        });
      });
    });
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

  function renderChecklist(host, d) {
    var modules = d.modules || [];
    if (!modules.length) return false;
    modules.forEach(function (m) {
      var box = el('div', 'module');
      var head = el('div', 'module-head');
      head.appendChild(el('span', 'title',
        m.title + (m.week != null ? ' · week ' + m.week : '')));
      head.appendChild(el('span', 'count', m.done + '/' + m.total));
      box.appendChild(head);
      var ul = el('ul');
      (m.items || []).forEach(function (item) {
        var li = el('li', item.done ? 'done' : '');
        li.appendChild(el('span', 'tick', item.done ? '✓' : '○'));
        li.appendChild(el('span', 'item-title', item.title));
        ul.appendChild(li);
      });
      box.appendChild(ul);
      host.appendChild(box);
    });
  }

  // --- <govkit-tasks> ------------------------------------------------------
  // Open tracker work. Row links prefer the tasks-app board deep link
  // (<data-tasks-app>/p/<project_slug>/board?story=<ref>), else external_url.

  function renderTasks(host, d) {
    var tasks = d.tasks || [];
    var limit = parseInt(host.dataset.limit || '6', 10);
    tasks = tasks.slice(0, limit > 0 ? limit : 6);
    if (!tasks.length) return false;

    var tasksApp = host.dataset.tasksApp && host.dataset.tasksApp.replace(/\/+$/, '');
    var table = el('table');
    var tbody = el('tbody');
    tasks.forEach(function (t) {
      var tr = el('tr');
      var tdSubject = el('td');
      var href = null;
      if (tasksApp && t.project_slug && t.ref != null) {
        href = safeHref(tasksApp + '/p/' + encodeURIComponent(t.project_slug) +
          '/board?story=' + encodeURIComponent(t.ref));
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
      tr.appendChild(tdSubject);
      tr.appendChild(el('td', 'assignee', t.assignee_label || ''));
      tr.appendChild(el('td', 'status', t.status || ''));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  // --- <govkit-money> ------------------------------------------------------

  function money(value, currency) {
    var n = num(value);
    if (n == null) return '';
    return n.toLocaleString(undefined, { minimumFractionDigits: 2 }) +
      (currency ? ' ' + currency : '');
  }

  function renderMoney(host, d) {
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
      tr.appendChild(el('td', 'name', p.name));
      tr.appendChild(el('td', 'kind', p.kind + ' · ' + p.status));
      tr.appendChild(el('td', 'num',
        p.budget_total == null
          ? money(p.paid_total, d.currency)
          : money(p.paid_total, null) + ' / ' + money(p.budget_total, d.currency)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  // --- element registration ------------------------------------------------

  function define(tag, path, render) {
    if (customElements.get(tag)) return;
    customElements.define(tag, class extends HTMLElement {
      connectedCallback() { mount(this, path, render); }
    });
  }

  define('govkit-pie', 'pie/orgs/{org}/summary/', renderPie);
  define('govkit-feed', 'pie/orgs/{org}/summary/', renderFeed);
  define('govkit-checklist', 'orgs/{org}/checklist/', renderChecklist);
  define('govkit-tasks', 'tasksources/orgs/{org}/tasks/open/', renderTasks);
  define('govkit-money', 'projects/orgs/{org}/portfolio/', renderMoney);
})();
