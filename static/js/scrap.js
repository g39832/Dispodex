/* Scrap: pick what was moved (with a running total), filters that apply on change, and confirm before deleting. */
(function () {
  'use strict';
  var P = window.Pinksheet;

  // Filters and the import's column choices apply as soon as they change.
  document.querySelectorAll('[data-scrap-filters] [data-autosubmit], [data-scrap-mapping] [data-autosubmit]').forEach(function (input) {
    input.addEventListener('change', function () { input.form.submit(); });
  });

  // "12,5" from a phone keyboard means 12.5 pounds.
  var weight = document.querySelector('[data-scrap-weight]');
  if (weight) {
    weight.addEventListener('change', function () {
      if (/^\d+,\d{1,2}$/.test(weight.value.trim())) weight.value = weight.value.trim().replace(',', '.');
    });
  }

  // Live scale: show the weight about once a second; "Use scale weight" copies a steady reading into the form.
  var scaleBox = document.querySelector('[data-scrap-scale]');
  if (scaleBox && weight) {
    var valueEl = scaleBox.querySelector('[data-scale-value]');
    var statusEl = scaleBox.querySelector('[data-scale-status]');
    var useBtn = scaleBox.querySelector('[data-scale-use]');
    var source = document.querySelector('[data-scale-source]');
    var latest = null, timer = null, busy = false;
    var trim = function (text) { return String(text).replace(/\.?0+$/, ''); };
    var show = function (state, value, status) {
      scaleBox.dataset.state = state;
      valueEl.textContent = value;
      statusEl.textContent = status;
    };
    var poll = function () {
      if (busy || document.hidden) return;
      busy = true;
      fetch(scaleBox.dataset.scrapScale, { headers: { Accept: 'application/json' }, cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.online || data.lbs === null) {
            latest = null;
            show('offline', '—', data.error || 'Scale offline');
          } else {
            latest = data.stable ? trim(data.lbs) : null;
            show(data.stable ? 'stable' : 'settling', trim(data.lbs) + ' lbs', data.stable ? 'Steady' : 'Settling…');
          }
        })
        .catch(function () { latest = null; show('offline', '—', 'Can’t reach Dispodex'); })
        .then(function () {
          busy = false;
          useBtn.disabled = !latest || parseFloat(latest) <= 0;
        });
    };
    var start = function () { if (!timer) { poll(); timer = setInterval(poll, 1000); } };
    var stop = function () { clearInterval(timer); timer = null; };
    document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else start(); });
    useBtn.addEventListener('click', function () {
      if (!latest) return;
      weight.value = latest;
      source.value = 'scale';
      weight.classList.add('is-from-scale');
      weight.focus();
    });
    // Typing over the scale's number makes it a typed-in weight again.
    weight.addEventListener('input', function () {
      source.value = 'typed';
      weight.classList.remove('is-from-scale');
    });
    start();
  }

  // Waiting to move: tick rows, see how many pounds that is, then mark them moved in one go.
  var moveForm = document.querySelector('[data-scrap-move]');
  if (moveForm) {
    var boxes = moveForm.querySelectorAll('input[name="entry"]');
    var all = moveForm.querySelector('[data-select-all]');
    var summary = moveForm.querySelector('[data-selected-summary]');
    var button = moveForm.querySelector('[data-move-selected]');
    var update = function () {
      var count = 0, total = 0;
      boxes.forEach(function (box) {
        if (box.checked) { count += 1; total += parseFloat(box.dataset.lbs) || 0; }
        box.closest('li').classList.toggle('is-selected', box.checked);
      });
      if (all) {
        all.checked = count > 0 && count === boxes.length;
        all.indeterminate = count > 0 && count < boxes.length;
      }
      if (summary) {
        summary.textContent = count
          ? count + ' selected · ' + total.toLocaleString(undefined, { maximumFractionDigits: 2 }) + ' lbs'
          : 'Tick what was carried over';
      }
      if (button) button.disabled = !count;
    };
    boxes.forEach(function (box) { box.addEventListener('change', update); });
    if (all) {
      all.addEventListener('change', function () {
        boxes.forEach(function (box) { box.checked = all.checked; });
        update();
      });
    }
    update();
  }

  // One click saves once, even on a slow connection.
  document.querySelectorAll('[data-scrap-weigh], [data-scrap-move], [data-scrap-upload]').forEach(function (form) {
    form.addEventListener('submit', function () {
      setTimeout(function () {
        form.querySelectorAll('button[type="submit"]').forEach(function (b) { b.disabled = true; });
      }, 0);
    });
  });

  document.querySelectorAll('[data-scrap-delete]').forEach(function (del) {
    del.addEventListener('submit', function (e) {
      if (del.dataset.confirmed) return;
      e.preventDefault();
      P.confirm({
        title: 'Delete this scrap entry?',
        body: del.dataset.title + ' is removed from the scrap totals for everyone.',
        confirmLabel: 'Delete', danger: true
      }).then(function (ok) {
        if (!ok) return;
        del.dataset.confirmed = '1';
        del.submit();
      });
    });
  });
})();
