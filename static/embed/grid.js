// <baobab-grid> — a dashboard a person can arrange, and that stays arranged.
//
// The page lists its cards as children, in default order, each with an id and a
// width out of 12:
//
//   <baobab-grid data-up="https://dash.workers.vc" data-dashboard="cohort">
//     <section class="card" data-card="pie" data-w="5"> <h2>The pie</h2> ... </section>
//     ...
//   </baobab-grid>
//   <script src="https://dash.workers.vc/static/embed/grid.js" defer></script>
//
// What a person can do: drag a card by its heading, drag its side to make it wider
// or narrower, hide it (x), bring it back (Add a card), see one card at a time, and
// reset to the page's default (with undo). Their arrangement is saved in GovKit
// (GET/PUT/DELETE /api/v1/accounts/me/layouts/<dashboard>/) and only they change it.
//
// Cards come and go on their own: a card the page removes (not for this person) or
// that sets itself `hidden` (nothing to show) leaves the grid and comes back if it
// fills in. That is never saved as the person's choice.
//
// Heights follow content. On a narrow screen the grid is one column in the saved
// order, and nothing is dragged or saved there.
//
// Built on GridStack.js (MIT, vendor/). Same conventions as govkit.js: vanilla JS,
// no shadow DOM, textContent-only writes, quiet failure.

(function () {
  'use strict';
  if (window.customElements && customElements.get('baobab-grid')) return;

  var HERE = (document.currentScript && document.currentScript.src) || '';
  var BASE = HERE.slice(0, HERE.lastIndexOf('/') + 1);
  var COLS = 12;
  var NARROW = 760;

  function loadGridStack() {
    if (window.GridStack) return Promise.resolve(window.GridStack);
    if (!document.querySelector('link[data-gridstack]')) {
      var l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = BASE + 'vendor/gridstack.min.css';
      l.dataset.gridstack = '';
      document.head.appendChild(l);
    }
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = BASE + 'vendor/gridstack-all.js';
      s.onload = function () { window.GridStack ? resolve(window.GridStack) : reject(); };
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function ensureStyles() {
    if (document.getElementById('baobab-grid-styles')) return;
    var s = document.createElement('style');
    s.id = 'baobab-grid-styles';
    s.textContent = [
      'baobab-grid { display: block; }',
      'baobab-grid .bg-tools { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 4px 14px; font-size: 12.5px; padding: 0 8px 6px; min-height: 22px; }',
      'baobab-grid .bg-tools button { font: inherit; color: var(--muted, #736b5c); background: none; border: 0; padding: 2px 0; cursor: pointer; }',
      'baobab-grid .bg-tools button:hover { color: var(--ink, #26221c); text-decoration: underline; }',
      'baobab-grid .bg-tools button[aria-pressed="true"] { color: var(--ink, #26221c); font-weight: 600; }',
      'baobab-grid .bg-tools .bg-note { color: var(--muted, #736b5c); }',
      'baobab-grid .bg-tools a { color: var(--muted, #736b5c); }',
      'baobab-grid .bg-tools a:hover { color: var(--ink, #26221c); }',
      'baobab-grid .bg-add { position: relative; }',
      'baobab-grid .bg-add ul { position: absolute; right: 0; top: 100%; z-index: 20; list-style: none; margin: 4px 0 0; padding: 4px 0; min-width: 12rem; background: var(--surface, #fff); border: 1px solid var(--hairline, #e6e1d8); border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); }',
      'baobab-grid .bg-add ul[hidden] { display: none; }',
      'baobab-grid .bg-add li button { display: block; width: 100%; text-align: left; padding: 6px 12px; color: var(--ink, #26221c); }',
      'baobab-grid .bg-tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 0 8px 10px; }',
      'baobab-grid .bg-tabs[hidden] { display: none; }',
      'baobab-grid .bg-tabs button { font: inherit; font-size: 13px; color: var(--ink-2, #5d574d); background: none; border: 1px solid var(--hairline, #e6e1d8); border-radius: 999px; padding: 4px 12px; cursor: pointer; }',
      'baobab-grid .bg-tabs button[aria-selected="true"] { color: var(--ink, #26221c); border-color: var(--ink-2, #5d574d); font-weight: 600; }',
      // the heading is the drag handle; the hide control sits in it
      'baobab-grid .grid-stack-item .bg-handle { cursor: grab; }',
      'baobab-grid.bg-static .grid-stack-item .bg-handle { cursor: auto; }',
      'baobab-grid .grid-stack-item.ui-draggable-dragging .bg-handle { cursor: grabbing; }',
      'baobab-grid .bg-hide { font: inherit; font-size: 15px; line-height: 1; color: var(--muted, #736b5c); background: none; border: 0; padding: 0 2px; margin-left: 8px; cursor: pointer; opacity: 0; }',
      'baobab-grid .grid-stack-item:hover .bg-hide, baobab-grid .bg-hide:focus-visible { opacity: 1; }',
      'baobab-grid.bg-static .bg-hide { display: none; }',
      'baobab-grid .bg-out { display: none; }',
      'baobab-grid .grid-stack-item-content { overflow: visible !important; }',
      'baobab-grid .grid-stack-item-content > * { margin: 0; }',
      'baobab-grid .grid-stack-placeholder > .placeholder-content { border: 1px dashed var(--hairline, #cbc4b6); border-radius: 10px; background: transparent; }',
      // narrow screens: one column in normal flow, ordered by syncStatic()
      'baobab-grid.bg-narrow .grid-stack { display: flex; flex-direction: column; gap: 12px; height: auto !important; min-height: 0 !important; }',
      'baobab-grid.bg-narrow .grid-stack-item { position: relative !important; top: auto !important; left: auto !important; width: 100% !important; height: auto !important; transform: none !important; }',
      'baobab-grid.bg-narrow .grid-stack-item > .grid-stack-item-content { position: relative !important; inset: auto !important; }',
      'baobab-grid.bg-narrow .ui-resizable-handle { display: none !important; }',
      // one at a time: the chosen card, full width, in normal flow
      'baobab-grid.bg-one .grid-stack { height: auto !important; min-height: 0 !important; }',
      'baobab-grid.bg-one .grid-stack-item { display: none; }',
      'baobab-grid.bg-one .grid-stack-item.bg-current { display: block; position: relative !important; top: auto !important; left: auto !important; width: 100% !important; height: auto !important; transform: none !important; }',
      'baobab-grid.bg-one .grid-stack-item.bg-current > .grid-stack-item-content { position: relative !important; inset: auto !important; }',
      'baobab-grid.bg-one .ui-resizable-handle { display: none !important; }',
    ].join('\n');
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Default positions: pack the cards left to right in page order, 12 columns a
  // row. y is only an ordering key; heights come from content and the grid packs up.
  function defaults(cards) {
    var out = {}, x = 0, row = 0;
    cards.forEach(function (c) {
      if (x + c.w > COLS) { x = 0; row += 1; }
      out[c.id] = { x: x, y: row * 100, w: c.w };
      x += c.w;
    });
    return out;
  }

  class BaobabGrid extends HTMLElement {
    connectedCallback() {
      if (this._started) return;
      this._started = true;
      ensureStyles();
      var self = this;
      this.up = (this.dataset.up || '').replace(/\/$/, '');
      this.dashboard = this.dataset.dashboard || 'default';
      this.cards = Array.prototype.filter.call(this.children, function (n) {
        return n.dataset && n.dataset.card;
      }).map(function (n) {
        var w = parseInt(n.dataset.w, 10);
        return { id: n.dataset.card, node: n, w: w > 0 && w <= COLS ? w : 4 };
      });
      this.byId = {};
      this.cards.forEach(function (c) { self.byId[c.id] = c; });
      this.defaultPos = defaults(this.cards);
      this.saved = { items: {}, hidden: [], view: 'grid', current: '' };
      this.canSave = false;

      this.buildFrame();
      Promise.all([loadGridStack(), this.fetchLayout()])
        .then(function (r) { self.start(r[0]); })
        .catch(function () { self.fallback(); });
    }

    // GridStack could not load: the cards stay where the page put them, as a plain
    // stack. Nothing to arrange, nothing lost.
    fallback() {
      this.tools.hidden = true;
      var self = this;
      this.cards.forEach(function (c) { if (c.node.parentNode === c.content) self.appendChild(c.node); });
      this.gridEl.remove();
    }

    fetchLayout() {
      var self = this;
      if (!this.up) return Promise.resolve();
      return fetch(this.layoutUrl(), { credentials: 'include' })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(function (data) {
          var l = (data && data.layout) || {};
          self.canSave = true;
          self.saved = {
            items: l.items && typeof l.items === 'object' && !Array.isArray(l.items) ? l.items : {},
            hidden: Array.isArray(l.hidden) ? l.hidden.filter(function (id) { return self.byId[id]; }) : [],
            view: l.view === 'one' ? 'one' : 'grid',
            current: typeof l.current === 'string' ? l.current : '',
          };
        })
        .catch(function () { self.canSave = false; });
    }

    layoutUrl() {
      return this.up + '/api/v1/accounts/me/layouts/' + encodeURIComponent(this.dashboard) + '/';
    }

    buildFrame() {
      var self = this;
      this.tools = el('div', 'bg-tools');
      this.note = el('span', 'bg-note');
      this.btnGrid = el('button', null, 'All cards');
      this.btnOne = el('button', null, 'One at a time');
      this.addWrap = el('span', 'bg-add');
      this.btnAdd = el('button', null, 'Add a card');
      this.addList = el('ul');
      this.addList.hidden = true;
      this.addWrap.append(this.btnAdd, this.addList);
      this.btnReset = el('button', null, 'Reset');
      [this.btnGrid, this.btnOne, this.btnAdd, this.btnReset].forEach(function (b) { b.type = 'button'; });
      this.btnAdd.setAttribute('aria-haspopup', 'true');
      this.btnAdd.setAttribute('aria-expanded', 'false');
      this.tools.append(this.note, this.btnGrid, this.btnOne, this.addWrap, this.btnReset);
      // Links the page wants beside these controls (e.g. another view of the same
      // dashboard) are marked data-tool and join the row.
      Array.prototype.slice.call(this.querySelectorAll(':scope > [data-tool]')).forEach(function (t) {
        self.tools.appendChild(t);
      });
      this.tools.hidden = true;

      this.tabs = el('div', 'bg-tabs');
      this.tabs.setAttribute('role', 'tablist');
      this.tabs.hidden = true;

      this.gridEl = el('div', 'grid-stack');
      // Every card stays in the page from the start, parked (display:none) until it
      // has something to show: its components load, and the page's own scripts can
      // still find it to decide who it is for.
      this.cards.forEach(function (c) {
        var item = el('div', 'bg-item bg-out');
        var content = el('div', 'grid-stack-item-content');
        item.appendChild(content);
        content.appendChild(c.node);
        self.gridEl.appendChild(item);
        c.item = item;
        c.content = content;
        self.decorate(c);
      });
      this.prepend(this.tools, this.tabs, this.gridEl);

      this.btnGrid.addEventListener('click', function () { self.setView('grid', true); });
      this.btnOne.addEventListener('click', function () { self.setView('one', true); });
      this.btnReset.addEventListener('click', function () { self.reset(); });
      this.btnAdd.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = self.addList.hidden;
        self.addList.hidden = !open;
        self.btnAdd.setAttribute('aria-expanded', String(open));
      });
      document.addEventListener('click', function () {
        self.addList.hidden = true;
        self.btnAdd.setAttribute('aria-expanded', 'false');
      });
    }

    // The card's heading becomes the drag handle and carries the hide control.
    decorate(c) {
      var self = this;
      var head = c.node.querySelector('.card-head, .card-header, h2');
      if (!head) return;
      head.classList.add('bg-handle');
      var hide = el('button', 'bg-hide', '×');
      hide.type = 'button';
      hide.setAttribute('aria-label', 'Hide ' + this.title(c));
      hide.addEventListener('mousedown', function (e) { e.stopPropagation(); });
      hide.addEventListener('click', function (e) {
        e.stopPropagation();
        self.hideCard(c.id);
      });
      head.appendChild(hide);
    }

    title(c) {
      if (c.node.dataset.title) return c.node.dataset.title;
      var t = c.node.querySelector('.card-title, h2');
      return t ? t.firstChild && t.firstChild.nodeType === 3 ? t.firstChild.textContent.trim() : t.textContent.trim() : c.id;
    }

    start(GS) {
      var self = this;
      this.grid = GS.init({
        column: COLS,
        cellHeight: 8,
        margin: 8,
        float: false,
        animate: true,
        sizeToContent: true,
        handle: '.bg-handle',
        resizable: { handles: 'e,w' },
      }, this.gridEl);

      this.present = {};
      this.cards.forEach(function (c) {
        self.watch(c);
      });
      this.relayout();
      // Signed out, or GovKit unreachable: an arrangement could not be kept, so
      // nothing can be moved and only the page's own links show in the row.
      if (!this.canSave) {
        [this.btnGrid, this.btnOne, this.addWrap, this.btnReset].forEach(function (b) { b.hidden = true; });
      }
      this.tools.hidden = false;
      this.syncStatic();
      window.addEventListener('resize', function () { self.syncStatic(); });

      this.grid.on('dragstop resizestop', function () { self.save(); self.syncStatic(); });
      this.renderAddList();
      this.setView(this.saved.view, false);
    }

    // A card is on the grid when it is still in the page, not hidden by itself, and
    // not hidden by the person.
    shouldShow(c) {
      return c.node.isConnected && c.node.parentNode === c.content && !c.node.hidden
        && this.saved.hidden.indexOf(c.id) === -1;
    }

    watch(c) {
      var self = this;
      var sync = function () { self.syncCard(c); };
      new MutationObserver(sync).observe(c.node, { attributes: true, attributeFilter: ['hidden'] });
      new MutationObserver(sync).observe(c.content, { childList: true });
      if (window.ResizeObserver) {
        new ResizeObserver(function () {
          if (self.present[c.id] && self.grid) self.grid.resizeToContent(c.item);
        }).observe(c.node);
      }
    }

    syncCard(c) {
      if (this.shouldShow(c) !== !!this.present[c.id]) this.relayout();
    }

    // Place every card that should show, in the order it was put (top to bottom,
    // left to right). Cards appear at different moments as their data arrives;
    // laying them all out again in that order is what keeps the result the same
    // every time, whichever card answered first.
    relayout() {
      var self = this;
      var show = this.cards.filter(function (c) { return self.shouldShow(c); });
      show.sort(function (a, b) {
        var p = self.intended(a.id), q = self.intended(b.id);
        return p.y - q.y || p.x - q.x;
      });
      this.grid.batchUpdate();
      this.cards.forEach(function (c) {
        if (!self.present[c.id]) return;
        self.grid.removeWidget(c.item, false, false);
        delete self.present[c.id];
        c.item.className = 'bg-item bg-out';
        c.item.removeAttribute('style');
      });
      // Heights are only known once each card has rendered, so saved rows can
      // overlap while cards grow. Spread the rows far apart and let the grid pack
      // them up: the order holds, the gaps close.
      var rows = [];
      show.forEach(function (c) {
        var y = self.intended(c.id).y;
        if (rows.indexOf(y) === -1) rows.push(y);
      });
      rows.sort(function (a, b) { return a - b; });
      show.forEach(function (c) {
        var p = self.intended(c.id);
        c.item.classList.remove('bg-out');
        c.item.classList.add('grid-stack-item');
        self.grid.makeWidget(c.item, {
          id: c.id, x: p.x, y: rows.indexOf(p.y) * 1000, w: p.w || c.w, sizeToContent: true,
        });
        self.present[c.id] = true;
      });
      this.grid.batchUpdate(false);
      this.syncStatic();
      this.renderTabs();
    }

    // A narrow screen shows the cards as one column, in the order a person reads
    // the wide layout (top to bottom, left to right). The wide layout itself is
    // untouched, so nothing a person arranged is lost by opening it on a phone.
    syncStatic() {
      if (!this.grid) return;
      var narrow = window.innerWidth <= NARROW;
      var fixed = narrow || this.saved.view === 'one' || !this.canSave;
      this.grid.setStatic(fixed);
      this.classList.toggle('bg-static', fixed);
      this.classList.toggle('bg-narrow', narrow);
      var self = this;
      this.inOrder().forEach(function (id, i) { self.byId[id].item.style.order = i; });
    }

    // Where a card was put on a wide screen: what the person saved, else the default.
    intended(id) {
      return this.saved.items[id] || this.defaultPos[id];
    }

    // Positions of what is on the grid now, merged over what was saved, so a card
    // that is absent today keeps its place for the day it comes back.
    collect() {
      var items = Object.assign({}, this.saved.items);
      (this.grid.engine.nodes || []).forEach(function (n) {
        if (n.id) items[n.id] = { x: n.x, y: n.y, w: n.w };
      });
      return items;
    }

    save() {
      if (!this.grid || window.innerWidth <= NARROW) return;
      this.saved.items = this.collect();
      this.put(this.saved);
    }

    put(layout) {
      var self = this;
      if (!this.canSave) return Promise.resolve();
      this.note.textContent = '';
      return fetch(this.layoutUrl(), {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Govkit-Embed': '1' },
        body: JSON.stringify({ layout: layout }),
      })
        .then(function (r) { if (!r.ok) throw new Error(r.status); })
        .catch(function () { self.note.textContent = 'This arrangement is not saved.'; });
    }

    hideCard(id) {
      if (this.saved.hidden.indexOf(id) !== -1) return;
      this.saved.items = this.collect();
      this.saved.hidden = this.saved.hidden.concat([id]);
      this.syncCard(this.byId[id]);
      this.renderAddList();
      this.put(this.saved);
    }

    showCard(id) {
      this.saved.hidden = this.saved.hidden.filter(function (h) { return h !== id; });
      var c = this.byId[id];
      delete this.saved.items[id];
      this.defaultPos[id] = { x: 0, y: 100000, w: c.w };
      this.syncCard(c);
      this.renderAddList();
      this.save();
      if (this.saved.view === 'one') this.select(id);
    }

    renderAddList() {
      var self = this;
      this.addList.textContent = '';
      this.saved.hidden.forEach(function (id) {
        var c = self.byId[id];
        if (!c || !c.node.isConnected) return;
        var li = el('li');
        var b = el('button', null, self.title(c));
        b.type = 'button';
        b.addEventListener('click', function () {
          self.addList.hidden = true;
          self.showCard(id);
        });
        li.appendChild(b);
        self.addList.appendChild(li);
      });
      this.addWrap.hidden = !this.canSave || !this.addList.children.length;
    }

    reset() {
      var self = this;
      var before = JSON.parse(JSON.stringify(Object.assign({}, this.saved, { items: this.collect() })));
      this.applyLayout({ items: {}, hidden: [], view: 'grid', current: '' });
      if (this.canSave) {
        fetch(this.layoutUrl(), {
          method: 'DELETE', credentials: 'include', headers: { 'X-Govkit-Embed': '1' },
        }).catch(function () {});
      }
      this.note.textContent = '';
      var undo = el('button', null, 'Undo');
      undo.type = 'button';
      undo.addEventListener('click', function () {
        self.applyLayout(before);
        self.put(before);
        self.note.textContent = '';
      });
      this.note.append('Reset. ', undo);
      setTimeout(function () { if (undo.isConnected) self.note.textContent = ''; }, 10000);
    }

    applyLayout(layout) {
      this.saved = {
        items: layout.items || {}, hidden: layout.hidden || [],
        view: layout.view || 'grid', current: layout.current || '',
      };
      this.defaultPos = defaults(this.cards);
      this.relayout();
      this.renderAddList();
      this.setView(this.saved.view, false);
    }

    setView(view, persist) {
      this.saved.view = view;
      this.classList.toggle('bg-one', view === 'one');
      this.btnGrid.setAttribute('aria-pressed', String(view === 'grid'));
      this.btnOne.setAttribute('aria-pressed', String(view === 'one'));
      this.tabs.hidden = view !== 'one';
      this.syncStatic();
      this.renderTabs();
      if (persist) { this.saved.items = this.collect(); this.put(this.saved); }
    }

    renderTabs() {
      var self = this;
      if (this.saved.view !== 'one' || !this.grid) return;
      var order = this.inOrder();
      if (order.indexOf(this.saved.current) === -1) this.saved.current = order[0] || '';
      this.tabs.textContent = '';
      order.forEach(function (id) {
        var b = el('button', null, self.title(self.byId[id]));
        b.type = 'button';
        b.setAttribute('role', 'tab');
        b.setAttribute('aria-selected', String(id === self.saved.current));
        b.addEventListener('click', function () { self.select(id, true); });
        self.tabs.appendChild(b);
      });
      this.cards.forEach(function (c) { c.item.classList.toggle('bg-current', c.id === self.saved.current); });
    }

    select(id, persist) {
      this.saved.current = id;
      this.renderTabs();
      if (persist) this.put(Object.assign({}, this.saved, { items: this.collect() }));
    }

    // Cards on the grid in reading order: top to bottom, left to right.
    // On a narrow screen the grid's own rows are distorted by tall content, so the
    // order comes from where the cards were put on a wide one.
    inOrder() {
      var self = this;
      var narrow = window.innerWidth <= NARROW;
      var nodes = (this.grid.engine.nodes || []).filter(function (n) { return n.id; }).map(function (n) {
        var p = narrow ? self.intended(n.id) : n;
        return { id: n.id, x: p.x, y: p.y };
      });
      nodes.sort(function (a, b) { return a.y - b.y || a.x - b.x; });
      return nodes.map(function (n) { return n.id; });
    }
  }

  customElements.define('baobab-grid', BaobabGrid);
})();
