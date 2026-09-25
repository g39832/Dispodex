/* Dispodex — shared front-end helpers used on every page.
 * Exposed as window.Pinksheet (the code name; the app is called Dispodex on screen):
 *   Pinksheet.api(url, {method, body})   JSON fetch with the CSRF token
 *   Pinksheet.toast(message, 'ok'|'err')  small notice at the bottom
 *   Pinksheet.confirm({title, body, ...}) promise-based confirm dialog
 *   Pinksheet.lightbox(src)               full-screen photo viewer
 */
(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

  function icon(name) {
    return '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ── API ──────────────────────────────────────────────────────────── */
  function api(url, options) {
    options = options || {};
    var init = {
      method: options.method || 'GET',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken, 'Accept': 'application/json' },
      keepalive: !!options.keepalive,
      cache: 'no-store'
    };
    if (options.form) {
      init.body = options.form;
    } else if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    return fetch(url, init).catch(function () {
      throw new Error('Can’t reach Dispodex. Check the Wi-Fi or that the Dispodex computer is on, then try again.');
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        data = data || {};
        if (!response.ok || data.ok === false) {
          var fallback = response.status === 403 ? 'This page is out of date. Reload it and try again.'
            : response.status >= 500 ? 'Dispodex hit a problem saving that (' + response.status + '). Try again; if it keeps happening, check System.'
            : 'Request failed (' + response.status + ')';
          var error = new Error(data.error || data.message || fallback);
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  /* Upload with progress (fetch can't report upload progress). */
  function upload(url, formData, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-CSRFToken', csrfToken);
      xhr.upload.onprogress = function (evt) {
        if (evt.lengthComputable && onProgress) onProgress(Math.round(evt.loaded / evt.total * 100));
      };
      xhr.onload = function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) { data = {}; }
        if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) resolve(data);
        else reject(new Error(data.error || ('Upload failed (' + xhr.status + ')')));
      };
      xhr.onerror = function () { reject(new Error('Network error — is the server running?')); };
      xhr.send(formData);
    });
  }

  /* ── Toasts ───────────────────────────────────────────────────────── */
  var toastBox = document.getElementById('toasts');
  function toast(message, tone, action) {
    if (!toastBox || !message) return null;
    var el = document.createElement('div');
    el.className = 'toast ' + (tone === 'err' ? 'err' : 'ok');
    el.innerHTML = icon(tone === 'err' ? 'alert' : 'check') + '<span></span>';
    el.querySelector('span').textContent = message;
    if (action) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = action.label;
      btn.addEventListener('click', function () { el.remove(); action.onClick(); });
      el.appendChild(btn);
    }
    toastBox.appendChild(el);
    setTimeout(function () { el.remove(); }, action ? 7000 : (tone === 'err' ? 6000 : 3500));
    return el;
  }
  document.querySelectorAll('template[data-flash]').forEach(function (tpl) {
    toast(tpl.content.textContent.trim(), tpl.getAttribute('data-flash'));
  });

  /* ── Confirm dialog ───────────────────────────────────────────────── */
  function confirmDialog(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'overlay';
      overlay.innerHTML =
        '<div class="dialog" role="alertdialog" aria-modal="true">' +
          '<div class="dialog-head"></div>' +
          '<div class="dialog-body"><p></p></div>' +
          '<div class="dialog-foot"><button type="button" class="btn" data-cancel>Cancel</button>' +
          '<button type="button" class="btn ' + (opts.danger ? 'btn-danger' : 'btn-primary') + '" data-ok></button></div>' +
        '</div>';
      overlay.querySelector('.dialog-head').textContent = opts.title || 'Are you sure?';
      overlay.querySelector('.dialog-body p').textContent = opts.body || '';
      overlay.querySelector('[data-ok]').textContent = opts.confirmLabel || 'Confirm';
      var input = null;
      if (opts.input) {
        input = document.createElement('input');
        input.className = 'input';
        input.value = opts.input.value || '';
        input.placeholder = opts.input.placeholder || '';
        input.maxLength = opts.input.maxLength || 80;
        input.autocomplete = 'name';
        overlay.querySelector('.dialog-body').appendChild(input);
      }
      if (opts.typeToConfirm) {
        input = document.createElement('input');
        input.className = 'input';
        input.placeholder = 'Type ' + opts.typeToConfirm + ' to confirm';
        overlay.querySelector('.dialog-body').appendChild(input);
      }
      var close = function (value) { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(value); };
      var ok = function () {
        if (opts.typeToConfirm && input.value.trim().toUpperCase() !== opts.typeToConfirm) {
          input.classList.add('is-invalid');
          input.focus();
          return;
        }
        close(opts.input ? input.value.trim() : true);
      };
      var onKey = function (e) {
        if (e.key === 'Escape') close(false);
        if (e.key === 'Enter') { e.preventDefault(); ok(); }
      };
      overlay.querySelector('[data-cancel]').addEventListener('click', function () { close(false); });
      overlay.querySelector('[data-ok]').addEventListener('click', ok);
      overlay.addEventListener('click', function (e) { if (e.target === overlay) close(false); });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(overlay);
      (input || overlay.querySelector('[data-ok]')).focus();
      if (input && opts.input) input.select();
    });
  }

  /* ── Lightbox ─────────────────────────────────────────────────────── */
  function lightbox(src, caption) {
    var box = document.createElement('div');
    box.className = 'lightbox';
    box.innerHTML = '<img alt=""><button type="button" class="btn btn-icon" aria-label="Close">' + icon('x') + '</button>';
    box.querySelector('img').src = src;
    box.querySelector('img').alt = caption || 'Photo';
    var close = function () { box.remove(); document.removeEventListener('keydown', onKey); };
    var onKey = function (e) { if (e.key === 'Escape') close(); };
    box.addEventListener('click', function (e) { if (e.target === box || e.target.closest('button')) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(box);
  }
  document.addEventListener('click', function (e) {
    var trigger = e.target.closest('[data-lightbox]');
    if (!trigger) return;
    e.preventDefault();
    lightbox(trigger.getAttribute('data-lightbox'), trigger.getAttribute('title'));
  });

  /* ── Theme ────────────────────────────────────────────────────────── */
  function applyThemeLabel() {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    document.querySelectorAll('[data-theme-label]').forEach(function (el) { el.textContent = dark ? 'Light mode' : 'Dark mode'; });
    document.querySelectorAll('[data-theme-toggle] use').forEach(function (use) { use.setAttribute('href', dark ? '#i-sun' : '#i-moon'); });
  }
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-theme-toggle]')) return;
    var dark = document.documentElement.getAttribute('data-theme') !== 'dark';
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    try { localStorage.setItem('pinksheet-theme', dark ? 'dark' : 'light'); } catch (err) {}
    applyThemeLabel();
  });
  applyThemeLabel();

  /* ── Mobile navigation ────────────────────────────────────────────── */
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-nav-toggle]')) {
      document.body.classList.toggle('nav-open');
      return;
    }
    if (document.body.classList.contains('nav-open') && !e.target.closest('.sidebar')) {
      document.body.classList.remove('nav-open');
    }
  });

  /* "New intake" always starts from a blank sheet. */
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-new-intake]')) return;
    try {
      var draft = localStorage.getItem('pinksheet:intake-draft');
      if (draft) localStorage.setItem('pinksheet:intake-draft-backup', draft);
      localStorage.removeItem('pinksheet:intake-draft');
    } catch (err) {}
  });

  /* ── Command palette (Ctrl+K) ─────────────────────────────────────── */
  var palette = document.getElementById('palette');
  var paletteInput = document.getElementById('palette-input');
  var paletteResults = document.getElementById('palette-results');
  var paletteState = { selected: -1, timer: null, controller: null, results: [] };

  function paletteRender(results, emptyText) {
    paletteState.results = results;
    paletteState.selected = results.length ? 0 : -1;
    if (!results.length) {
      paletteResults.innerHTML = '<div class="palette-empty"></div>';
      paletteResults.firstChild.textContent = emptyText;
      return;
    }
    paletteResults.innerHTML = results.map(function (r, index) {
      var detail = [r.what_is_it, r.brand_model, r.serial_number ? 'S/N ' + r.serial_number : '', r.where_it_goes]
        .filter(Boolean).join(' · ');
      return '<a class="palette-result' + (index === 0 ? ' is-selected' : '') + '" href="/intake/?sku=' + encodeURIComponent(r.sku_normalized) + '">' +
        icon('box') +
        '<span class="grow"><strong>' + escapeHtml(r.sku) + '</strong><div class="line2">' + escapeHtml(detail || '—') + '</div></span>' +
        '<span class="tag" data-status="' + escapeHtml(r.status) + '">' + escapeHtml(r.status_label) + '</span>' +
        (r.price ? '<span class="num muted">' + escapeHtml(r.price) + '</span>' : '') +
        '</a>';
    }).join('');
  }

  function paletteSearch(query) {
    if (paletteState.controller) paletteState.controller.abort();
    paletteState.controller = new AbortController();
    var url = query ? '/api/palette/?q=' + encodeURIComponent(query) : '/api/palette/?recent=1';
    return fetch(url, { signal: paletteState.controller.signal, credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        paletteRender(data.results || [], query ? 'Nothing matches “' + query + '”.' : 'No items yet.');
        return data.results || [];
      })
      .catch(function (err) { if (err.name !== 'AbortError') paletteRender([], 'Search is unavailable right now.'); return []; });
  }

  function paletteMove(delta) {
    var links = paletteResults.querySelectorAll('.palette-result');
    if (!links.length) return;
    paletteState.selected = Math.max(0, Math.min(links.length - 1, paletteState.selected + delta));
    links.forEach(function (l, i) { l.classList.toggle('is-selected', i === paletteState.selected); });
    links[paletteState.selected].scrollIntoView({ block: 'nearest' });
  }

  function paletteOpen() {
    if (!palette) return;
    palette.hidden = false;
    paletteInput.value = '';
    paletteSearch('');
    setTimeout(function () { paletteInput.focus(); }, 0);
  }
  function paletteClose() { if (palette) palette.hidden = true; }

  if (palette) {
    // A barcode scanner "types" very fast and ends with Enter: open the first match straight away.
    var scan = { buffer: '', last: 0 };
    paletteInput.addEventListener('keydown', function (e) {
      var now = Date.now();
      if (e.key === 'ArrowDown') { e.preventDefault(); paletteMove(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); paletteMove(-1); return; }
      if (e.key === 'Escape') { paletteClose(); return; }
      if (e.key === 'Enter') {
        e.preventDefault();
        var scanned = scan.buffer.length >= 4 && now - scan.last < 80;
        clearTimeout(paletteState.timer);
        paletteSearch(paletteInput.value.trim()).then(function (results) {
          var links = paletteResults.querySelectorAll('.palette-result');
          var target = links[scanned ? 0 : Math.max(0, paletteState.selected)];
          if (target) window.location.href = target.getAttribute('href');
        });
        scan.buffer = '';
        return;
      }
      if (e.key.length === 1) {
        scan.buffer = now - scan.last < 80 ? scan.buffer + e.key : e.key;
        scan.last = now;
      }
    });
    paletteInput.addEventListener('input', function () {
      clearTimeout(paletteState.timer);
      paletteState.timer = setTimeout(function () { paletteSearch(paletteInput.value.trim()); }, 160);
    });
    palette.addEventListener('click', function (e) { if (e.target === palette) paletteClose(); });
    document.addEventListener('click', function (e) {
      if (e.target.closest('[data-open-palette]')) { e.preventDefault(); paletteOpen(); }
    });
  }

  /* ── Keyboard shortcuts ───────────────────────────────────────────── */
  document.addEventListener('keydown', function (e) {
    var key = (e.key || '').toLowerCase();
    if ((e.ctrlKey || e.metaKey) && key === 'k') { e.preventDefault(); paletteOpen(); }
    if (e.altKey && key === 'n') { e.preventDefault(); window.location.href = '/intake/?new=1'; }
  });

  /* Close <details> menus when clicking elsewhere or picking an item. */
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details.menu[open]').forEach(function (menu) {
      if (!menu.contains(e.target) || e.target.closest('.menu-pop a, .menu-pop button')) menu.removeAttribute('open');
    });
  });

  /* ── Who is using this device (labels changes in item history) ─────── */
  var NAME_COOKIE = 'ps_name';
  function deviceName() {
    var m = document.cookie.match(/(?:^|;\s*)ps_name=([^;]*)/);
    try { return m ? decodeURIComponent(m[1]) : ''; } catch (e) { return ''; }
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-set-name]');
    if (!btn) return;
    confirmDialog({
      title: 'Who is using this device?',
      body: 'Your name is shown next to the changes you make, in the history of each item. It is saved on this device only.',
      confirmLabel: 'Save name',
      input: { value: deviceName(), placeholder: 'Your first name', maxLength: 40 }
    }).then(function (name) {
      if (name === false) return;
      var maxAge = name ? 60 * 60 * 24 * 400 : 0;
      document.cookie = NAME_COOKIE + '=' + encodeURIComponent(name) + '; path=/; max-age=' + maxAge + '; SameSite=Lax';
      document.querySelectorAll('[data-actor-label]').forEach(function (el) { el.textContent = name || 'This device'; });
      toast(name ? 'Changes from this device will show as ' + name + '.' : 'Name cleared for this device.', 'ok');
    });
  });
  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-history-toggle]');
    if (!toggle) return;
    var list = toggle.previousElementSibling;
    list.querySelectorAll('[data-history-more]').forEach(function (row) { row.hidden = false; });
    toggle.remove();
  });

  window.Pinksheet = {
    api: api,
    upload: upload,
    toast: toast,
    confirm: confirmDialog,
    lightbox: lightbox,
    icon: icon,
    escapeHtml: escapeHtml,
    csrfToken: csrfToken
  };
})();
