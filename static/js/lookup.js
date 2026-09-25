/* Lookup: status chips, live SKU suggestions, auto-apply filters, recent SKUs. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var form = document.getElementById('lookup-form');
  var statusField = form.querySelector('[name="status"]');
  var search = form.querySelector('[name="q"]');
  var datalist = document.getElementById('lookup-suggest');
  var RECENT = 'pinksheet:recent-skus';

  document.querySelectorAll('[data-status-chip]').forEach(function (chip) {
    chip.addEventListener('click', function (e) {
      e.preventDefault();
      statusField.value = chip.getAttribute('data-status-chip');
      form.submit();
    });
  });

  form.querySelectorAll('select').forEach(function (select) {
    select.addEventListener('change', function () { form.submit(); });
  });

  // "More filters" panel: its fields belong to the same form (form="lookup-form").
  var moreBtn = document.getElementById('more-filters-btn');
  var morePanel = document.getElementById('more-filters');
  if (moreBtn && morePanel) {
    moreBtn.addEventListener('click', function () {
      morePanel.hidden = !morePanel.hidden;
      moreBtn.setAttribute('aria-expanded', String(!morePanel.hidden));
      if (!morePanel.hidden) { var first = morePanel.querySelector('input, select'); if (first) first.focus(); }
    });
    document.getElementById('more-filters-reset').addEventListener('click', function () {
      morePanel.querySelectorAll('input').forEach(function (i) { i.value = ''; });
      morePanel.querySelectorAll('select').forEach(function (s) { s.selectedIndex = 0; });
    });
  }

  form.addEventListener('submit', function () {
    document.querySelectorAll('[form="lookup-form"], #lookup-form [name]').forEach(function (el) {
      if (el.name && !el.value && el.type !== 'hidden') el.disabled = true;
    });
    setTimeout(function () { document.querySelectorAll('[form="lookup-form"]:disabled, #lookup-form [name]:disabled').forEach(function (el) { el.disabled = false; }); }, 0);
  });

  var timer = null;
  search.addEventListener('input', function () {
    clearTimeout(timer);
    var q = search.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(function () {
      P.api('/api/suggestions/?q=' + encodeURIComponent(q)).then(function (d) {
        datalist.innerHTML = '';
        (d.results || []).forEach(function (r) {
          var option = document.createElement('option');
          option.value = r.value;
          option.label = r.label;
          datalist.appendChild(option);
        });
      }).catch(function () {});
    }, 200);
  });

  // Remember the last few SKUs opened from here.
  document.addEventListener('click', function (e) {
    var link = e.target.closest('[data-recent-sku]');
    if (!link) return;
    try {
      var sku = link.getAttribute('data-recent-sku');
      var list = JSON.parse(localStorage.getItem(RECENT) || '[]').filter(function (s) { return s !== sku; });
      list.unshift(sku);
      localStorage.setItem(RECENT, JSON.stringify(list.slice(0, 6)));
    } catch (err) {}
  });
  try {
    var host = document.getElementById('recent-skus');
    JSON.parse(localStorage.getItem(RECENT) || '[]').forEach(function (sku) {
      var a = document.createElement('a');
      a.className = 'chip';
      a.href = '/intake/?sku=' + encodeURIComponent(sku);
      a.textContent = sku;
      host.appendChild(a);
    });
    if (host.children.length) {
      var label = document.createElement('span');
      label.className = 'faint';
      label.textContent = 'Recent:';
      host.prepend(label);
    }
  } catch (err) {}
})();

/* Lookup: select several rows and change their status or "Ready" together. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var bar = document.getElementById('bulk-bar');
  if (!bar) return;
  var all = document.querySelector('[data-select-all]');
  var boxes = Array.prototype.slice.call(document.querySelectorAll('[data-select]'));
  var count = document.getElementById('bulk-count');
  var statusSelect = document.getElementById('bulk-status');

  function selected() { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); }
  function refresh() {
    var n = selected().length;
    bar.hidden = n === 0;
    count.textContent = n + ' selected';
    all.checked = n > 0 && n === boxes.length;
    all.indeterminate = n > 0 && n < boxes.length;
    boxes.forEach(function (b) { b.closest('tr').classList.toggle('is-selected', b.checked); });
  }
  all.addEventListener('change', function () { boxes.forEach(function (b) { b.checked = all.checked; }); refresh(); });
  boxes.forEach(function (b) { b.addEventListener('change', refresh); });
  document.getElementById('bulk-clear').addEventListener('click', function () { boxes.forEach(function (b) { b.checked = false; }); refresh(); });

  function apply(field, value, label) {
    var skus = selected();
    return P.confirm({
      title: label + '?',
      body: 'This changes ' + skus.length + ' item' + (skus.length === 1 ? '' : 's') + '. Each change is recorded in the item history.',
      confirmLabel: label
    }).then(function (yes) {
      if (!yes) return;
      bar.classList.add('is-busy');
      return P.api('/api/items/bulk-update/', { method: 'POST', body: { skus: skus, field: field, value: value } })
        .then(function (resp) {
          if (resp.failed && resp.failed.length) {
            P.toast(resp.updated + ' updated. Not changed: ' + resp.failed.map(function (f) { return f.sku + ' (' + f.error + ')'; }).join(', '), 'err');
            setTimeout(function () { location.reload(); }, 2500);
          } else {
            P.toast(resp.updated + ' item' + (resp.updated === 1 ? '' : 's') + ' updated.', 'ok');
            setTimeout(function () { location.reload(); }, 600);
          }
        })
        .catch(function (err) { P.toast(err.message, 'err'); })
        .then(function () { bar.classList.remove('is-busy'); });
    });
  }
  statusSelect.addEventListener('change', function () {
    var value = statusSelect.value;
    if (!value) return;
    var label = 'Move to ' + statusSelect.options[statusSelect.selectedIndex].text;
    apply('status', value, label).then(function () { statusSelect.value = ''; });
  });
  bar.querySelectorAll('[data-bulk-ready]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var ready = btn.getAttribute('data-bulk-ready') === '1';
      apply('ready', ready ? '1' : '0', ready ? 'Mark ready' : 'Mark not ready');
    });
  });
  refresh();
})();
