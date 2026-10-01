/* Status board: load cards, drag between lanes, quick toggles, delete + undo, QR tools. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var board = document.getElementById('board');
  var filterInput = document.getElementById('board-filter');
  var undoBtn = document.getElementById('undo-delete');
  var highlight = JSON.parse(document.getElementById('board-highlight').textContent || '""');
  var REVIEW_LABELS = { 0: 'INACTIVE', 1: 'ACTIVE', 2: 'SOLD' };

  function lane(status) { return board.querySelector('.lane[data-lane="' + status + '"]'); }

  function money(value) {
    return '$' + value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }
  function updateCounts() {
    board.querySelectorAll('.lane').forEach(function (l) {
      var visible = l.querySelectorAll('.card:not([hidden])');
      var total = 0;
      visible.forEach(function (card) { total += Number(card.dataset.value || 0); });
      l.querySelector('[data-count]').textContent = String(visible.length);
      l.querySelector('[data-total]').textContent = total > 0 ? money(total) : '';
    });
  }
  var CONDITION_TONE = { Scrap: 'red', Poor: 'red', Fair: 'amber', Good: 'green', Great: 'green', Excellent: 'green' };

  function cardHtml(c) {
    var esc = P.escapeHtml;
    return (c.thumb_id ? '<img class="card-cover" src="/photos/' + c.thumb_id + '/?thumb=1" alt="" loading="lazy" draggable="false">' : '') +
      '<div class="card-body">' +
        // Names wrap onto as many lines as they need: nothing is cut off (SKUs only at spaces).
        '<button type="button" class="card-more" data-act="menu" aria-haspopup="dialog" aria-label="More actions for ' + esc(c.sku) + '" title="Move, QR code, print, delete">' + P.icon('more') + '</button>' +
        '<a class="card-sku" href="/intake/?sku=' + encodeURIComponent(c.norm) + '" draggable="false">' + P.skuHtml(c.sku) + '</a>' +
        '<div class="card-what">' + esc(c.what || '—') + '</div>' +
        (c.brand ? '<div class="card-brand">' + esc(c.brand) + '</div>' : '') +
        '<div class="card-meta card-extra">' +
          (c.price ? '<span class="price num">$' + esc(Number(c.price).toFixed(2)) + '</span>' : '') +
          (c.qty > 1 ? '<span class="tag blue">×' + c.qty + '</span>' : '') +
          (c.condition ? '<span class="tag ' + (CONDITION_TONE[c.condition] || '') + '">' + esc(c.condition) + '</span>' : '') +
          '<span>' + esc(c.updated) + '</span>' +
        '</div>' +
        '<div class="card-meta card-status">' +
          '<button type="button" class="tag review-badge" data-review="' + c.reviewed + '" data-act="review">' + REVIEW_LABELS[c.reviewed] + '</button>' +
          '<label class="check card-ready"><input type="checkbox" data-act="ready"' + (c.ready ? ' checked' : '') + '> Ready</label>' +
        '</div>' +
      '</div>';
  }

  function buildCard(c) {
    var card = document.createElement('article');
    card.className = 'card';
    card.draggable = true;
    card.dataset.id = c.id;
    card.dataset.sku = c.norm;
    card.dataset.qr = c.qr_url;
    card.dataset.search = (c.sku + ' ' + (c.what || '') + ' ' + (c.brand || '')).toLowerCase();
    card.dataset.value = c.price ? String(Number(c.price) * (c.qty || 1)) : '0';
    card.innerHTML = cardHtml(c);
    return card;
  }

  function load() {
    P.api('/api/board/cards/').then(function (data) {
      Object.keys(data.lanes).forEach(function (status) {
        var body = lane(status).querySelector('.lane-body');
        body.innerHTML = '';
        data.lanes[status].forEach(function (c) { body.appendChild(buildCard(c)); });
        var hidden = (data.more || {})[status] || 0;
        if (hidden > 0) {
          var link = document.createElement('a');
          link.className = 'lane-more';
          link.href = '/lookup/?status=' + encodeURIComponent(status);
          link.textContent = 'Showing the newest ' + data.lanes[status].length + ' of ' + (data.totals || {})[status] +
            '. See all ' + (data.totals || {})[status] + ' in Lookup →';
          body.appendChild(link);
        }
      });
      applyFilter();
      if (highlight) {
        var target = board.querySelector('.card[data-sku="' + CSS.escape(highlight) + '"]');
        if (target) {
          setCollapsed(target.closest('.lane'), false);
          target.classList.add('is-highlight');
          target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
        } else {
          P.toast('No card found for ' + highlight, 'err');
        }
      }
    }).catch(function (err) {
      board.querySelectorAll('.lane-body').forEach(function (b) { b.innerHTML = '<p class="hint">Could not load cards: ' + P.escapeHtml(err.message) + '</p>'; });
    });
  }

  function update(sku, field, value) {
    return P.api('/api/items/' + encodeURIComponent(sku) + '/update/', { method: 'POST', keepalive: true, body: { field: field, value: value } });
  }

  /* Collapsible lanes (remembered on this device) */
  var COLLAPSED_KEY = 'dispodex:board-collapsed';
  function savedCollapsed() {
    try { return JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]'); } catch (e) { return []; }
  }
  function setCollapsed(l, collapsed) {
    if (!l) return;
    l.classList.toggle('is-collapsed', collapsed);
    var btn = l.querySelector('[data-lane-toggle]');
    var label = l.getAttribute('aria-label');
    btn.setAttribute('aria-expanded', String(!collapsed));
    btn.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + label);
    btn.title = collapsed ? 'Expand this stage' : 'Collapse this stage';
    btn.innerHTML = P.icon(collapsed ? 'chevron-right' : 'chevron-left');
    var list = savedCollapsed().filter(function (s) { return s !== l.dataset.lane; });
    if (collapsed) list.push(l.dataset.lane);
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify(list)); } catch (e) {}
  }
  savedCollapsed().forEach(function (status) { setCollapsed(lane(status), true); });
  board.addEventListener('click', function (e) {
    var l = e.target.closest('.lane');
    if (!l) return;
    if (e.target.closest('[data-lane-toggle]')) { setCollapsed(l, !l.classList.contains('is-collapsed')); return; }
    if (l.classList.contains('is-collapsed')) setCollapsed(l, false);  // tap a folded lane to open it
  });

  /* Detailed / compact cards (remembered on this device) */
  var VIEW_KEY = 'dispodex:board-view';
  function setView(view) {
    board.classList.toggle('is-compact', view === 'compact');
    document.querySelectorAll('[data-board-view]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-board-view') === view));
    });
    try { localStorage.setItem(VIEW_KEY, view); } catch (e) {}
  }
  document.querySelectorAll('[data-board-view]').forEach(function (b) {
    b.addEventListener('click', function () { setView(b.getAttribute('data-board-view')); });
  });
  var savedView = 'detailed';
  try { savedView = localStorage.getItem(VIEW_KEY) === 'compact' ? 'compact' : 'detailed'; } catch (e) {}
  setView(savedView);

  /* Filter */
  function applyFilter() {
    var q = (filterInput.value || '').trim().toLowerCase();
    board.querySelectorAll('.card').forEach(function (card) { card.hidden = q && card.dataset.search.indexOf(q) === -1; });
    updateCounts();
  }
  filterInput.addEventListener('input', applyFilter);
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && !e.target.closest('input, textarea, select')) { e.preventDefault(); filterInput.focus(); }
  });

  /* Drag and drop */
  var dragged = null;
  var fromLane = null;
  board.addEventListener('dragstart', function (e) {
    dragged = e.target.closest('.card');
    if (!dragged) return;
    fromLane = dragged.closest('.lane');
    dragged.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.dataset.sku);
  });
  board.addEventListener('dragend', function () {
    if (dragged) dragged.classList.remove('is-dragging');
    board.querySelectorAll('.is-drop-target').forEach(function (l) { l.classList.remove('is-drop-target'); });
    dragged = null;
  });
  board.addEventListener('dragover', function (e) {
    if (!dragged) return;
    var target = e.target.closest('.lane');
    if (!target) return;
    e.preventDefault();
    board.querySelectorAll('.is-drop-target').forEach(function (l) { if (l !== target) l.classList.remove('is-drop-target'); });
    target.classList.add('is-drop-target');
    var rect = board.getBoundingClientRect();
    if (e.clientX > rect.right - 60) board.scrollLeft += 14;
    if (e.clientX < rect.left + 60) board.scrollLeft -= 14;
  });
  board.addEventListener('drop', function (e) {
    var target = e.target.closest('.lane');
    if (!dragged || !target) return;
    e.preventDefault();
    target.classList.remove('is-drop-target');
    moveCard(dragged, target);
  });

  function moveCard(card, target) {
    var origin = card.closest('.lane');
    if (origin === target) return;
    target.querySelector('.lane-body').prepend(card);
    updateCounts();
    update(card.dataset.sku, 'status', target.dataset.lane).then(function (resp) {
      var badge = card.querySelector('.review-badge');
      badge.dataset.review = resp.item.reviewed;
      badge.textContent = REVIEW_LABELS[resp.item.reviewed];
    }).catch(function (err) {
      origin.querySelector('.lane-body').prepend(card);
      updateCounts();
      P.toast('Could not move ' + card.dataset.sku + ': ' + err.message, 'err');
    });
  }

  /* Lane picker: how cards move on phones and tablets, where dragging doesn't work. */
  function pickLane(card) {
    var current = card.closest('.lane');
    var overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = '<div class="dialog" role="dialog" aria-modal="true"><div class="dialog-head"></div>' +
      '<div class="dialog-body lane-picker"></div><div class="dialog-foot"><button type="button" class="btn" data-cancel>Cancel</button></div></div>';
    overlay.querySelector('.dialog-head').textContent = 'Move ' + card.dataset.sku + ' to…';
    var list = overlay.querySelector('.lane-picker');
    board.querySelectorAll('.lane').forEach(function (l) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn lane-pick';
      btn.disabled = l === current;
      var tag = l.querySelector('.lane-head .tag').cloneNode(true);
      btn.appendChild(tag);
      if (l === current) { var here = document.createElement('span'); here.className = 'faint'; here.textContent = 'Current'; btn.appendChild(here); }
      btn.addEventListener('click', function () { close(); moveCard(card, l); l.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' }); });
      list.appendChild(btn);
    });
    function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    overlay.addEventListener('click', function (e) { if (e.target === overlay || e.target.closest('[data-cancel]')) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    var first = list.querySelector('button:not([disabled])');
    if (first) first.focus();
  }

  /* Card actions menu: Move, QR code, print, open, delete. Never covers the card's names. */
  function openMenu(card) {
    var sku = card.dataset.sku;
    var qrOpen = !!card.querySelector('.card-qr');
    var items = [
      ['move', 'move', 'Move to another stage…'],
      ['qr', 'qr', qrOpen ? 'Hide QR code' : 'Show QR code'],
      ['print', 'printer', 'Print card'],
      ['open', 'script', 'Open full sheet'],
      ['delete', 'trash', 'Delete…']
    ];
    var overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = '<div class="dialog" role="dialog" aria-modal="true"><div class="dialog-head"></div>' +
      '<div class="dialog-body card-menu"></div><div class="dialog-foot"><button type="button" class="btn" data-cancel>Cancel</button></div></div>';
    overlay.querySelector('.dialog-head').textContent = sku;
    var list = overlay.querySelector('.card-menu');
    items.forEach(function (item) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn card-menu-item' + (item[0] === 'delete' ? ' btn-danger' : '');
      btn.setAttribute('data-menu-act', item[0]);
      btn.innerHTML = P.icon(item[1]) + '<span></span>';
      btn.querySelector('span').textContent = item[2];
      btn.addEventListener('click', function () {
        close();
        if (item[0] === 'open') { window.location.href = '/intake/?sku=' + encodeURIComponent(sku); return; }
        cardAction(card, item[0]);
      });
      list.appendChild(btn);
    });
    function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    overlay.addEventListener('click', function (e) { if (e.target === overlay || e.target.closest('[data-cancel]')) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    list.querySelector('button').focus();
  }

  /* Card buttons */
  var lastDeleted = null;
  board.addEventListener('click', function (e) {
    var el = e.target.closest('[data-act]');
    if (!el || !el.closest('.card')) return;
    cardAction(el.closest('.card'), el.dataset.act, el);
  });

  function cardAction(card, act, el) {
    if (act === 'menu') {
      openMenu(card);
    } else if (act === 'move') {
      pickLane(card);
    } else if (act === 'review') {
      var current = +el.dataset.review;
      if (current === 2) return;
      var next = current === 1 ? 0 : 1;
      update(card.dataset.sku, 'reviewed', next).then(function () {
        el.dataset.review = next;
        el.textContent = REVIEW_LABELS[next];
      }).catch(function (err) { P.toast(err.message, 'err'); });
    } else if (act === 'qr') {
      var drawer = card.querySelector('.card-qr');
      if (drawer) { drawer.remove(); return; }
      drawer = document.createElement('div');
      drawer.className = 'card-qr';
      var holder = document.createElement('div');
      drawer.appendChild(holder);
      card.appendChild(drawer);
      if (window.QRCode) new QRCode(holder, { text: card.dataset.qr, width: 132, height: 132, correctLevel: QRCode.CorrectLevel.H });
    } else if (act === 'print') {
      printCard(card.dataset.sku);
    } else if (act === 'delete') {
      P.confirm({ title: 'Delete ' + card.dataset.sku + '?', body: 'You can undo this right after.', confirmLabel: 'Delete', danger: true })
        .then(function (yes) {
          if (!yes) return;
          var parent = card.parentNode;
          var next = card.nextSibling;
          card.remove();
          updateCounts();
          P.api('/api/items/' + card.dataset.id + '/delete/', { method: 'POST', body: { confirm: 'DELETE' } })
            .then(function () {
              lastDeleted = { card: card, parent: parent, next: next };
              undoBtn.hidden = false;
              P.toast('Deleted ' + card.dataset.sku, 'ok', { label: 'Undo', onClick: undo });
            })
            .catch(function (err) { parent.insertBefore(card, next); updateCounts(); P.toast(err.message, 'err'); });
        });
    }
  }
  board.addEventListener('change', function (e) {
    if (e.target.dataset.act !== 'ready') return;
    var box = e.target;
    var card = box.closest('.card');
    update(card.dataset.sku, 'ready', box.checked ? 1 : 0).catch(function (err) {
      box.checked = !box.checked;
      P.toast(err.message, 'err');
    });
  });

  function undo() {
    P.api('/api/items/undo-delete/', { method: 'POST', body: {} }).then(function (resp) {
      if (lastDeleted && lastDeleted.card.dataset.sku === resp.item.sku_normalized) {
        lastDeleted.parent.insertBefore(lastDeleted.card, lastDeleted.next);
        updateCounts();
      } else {
        load();
      }
      lastDeleted = null;
      P.toast('Restored ' + resp.item.sku);
    }).catch(function (err) { P.toast(err.message, 'err'); });
  }
  undoBtn.addEventListener('click', undo);

  /* Print a card through a hidden frame */
  function printCard(sku) {
    var frame = document.createElement('iframe');
    frame.style.cssText = 'position:fixed;left:-9999px;width:8.5in;height:11in;border:0';
    frame.src = '/print/' + encodeURIComponent(sku) + '/?autoprint=1';
    document.body.appendChild(frame);
    setTimeout(function () { frame.remove(); }, 60000);
  }

  /* Camera QR scanner */
  var scanner = null;
  var overlay = document.getElementById('scanner');
  function stopScanner() {
    if (scanner) { try { scanner.clear(); } catch (err) {} scanner = null; }
    overlay.hidden = true;
  }
  document.getElementById('scan-btn').addEventListener('click', function () {
    if (!window.Html5QrcodeScanner) { P.toast('The QR scanner could not load.', 'err'); return; }
    overlay.hidden = false;
    scanner = new Html5QrcodeScanner('scanner-view', { fps: 10, qrbox: { width: 240, height: 240 } }, false);
    scanner.render(function (text) {
      var match = text.match(/\/card\/([^/?#]+)/) || text.match(/[?&]sku=([^&#]+)/i);
      var sku = match ? decodeURIComponent(match[1]) : text.trim();
      stopScanner();
      window.location.href = '/board/?highlight=' + encodeURIComponent(sku.toUpperCase());
    }, function () {});
  });
  document.getElementById('scanner-close').addEventListener('click', stopScanner);

  load();
})();
