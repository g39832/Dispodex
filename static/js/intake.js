/* Intake sheet behaviour.
 *
 * Sections:
 *   1. Form helpers & required-field progress
 *   2. Autosave drafts (server when a SKU is set, browser storage otherwise)
 *   3. eBay category picker
 *   4. Copy fields from another SKU
 *   5. Photos (upload, reorder, thumbnail, delete, download)
 *   6. QR code, printing, sticker, delete item, quick mode
 */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var config = JSON.parse(document.getElementById('intake-config').textContent);
  var form = document.getElementById('intake-form');
  var skuInput = document.getElementById('sku-input');
  var whatInput = document.getElementById('what-is-it');
  var statusSelect = document.getElementById('status-select');
  var LOCAL_DRAFT = 'pinksheet:intake-draft';
  var LOCAL_BACKUP = 'pinksheet:intake-draft-backup';
  var SKIP_FIELDS = { csrfmiddlewaretoken: 1, id: 1, save_and_new: 1 };

  /* ── 1. Form helpers ─────────────────────────────────────────────── */
  function currentSku() { return (skuInput.value || '').trim().toUpperCase(); }

  function formData() {
    var data = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || SKIP_FIELDS[el.name] || el.type === 'file') return;
      if (el.type === 'checkbox') data[el.name] = el.checked;
      else if (el.type === 'radio') { if (el.checked) data[el.name] = el.value; else if (!(el.name in data)) data[el.name] = ''; }
      else data[el.name] = el.value;
    });
    return data;
  }

  function applyData(data, options) {
    options = options || {};
    Object.keys(data || {}).forEach(function (name) {
      if (SKIP_FIELDS[name] || (options.skipSku && name === 'sku')) return;
      var fields = form.querySelectorAll('[name="' + name + '"]');
      fields.forEach(function (field) {
        var value = data[name];
        if (field.type === 'radio') field.checked = field.value === value;
        else if (field.type === 'checkbox') field.checked = !!value && value !== 'false';
        else if (field.type !== 'file') field.value = value == null ? '' : value;
      });
    });
    syncDerivedUi();
  }

  function formLooksEmpty() {
    var data = formData();
    return Object.keys(data).every(function (key) {
      var v = data[key];
      return key === 'status' || key === 'quantity' || v === '' || v === false;
    });
  }

  var requiredBar = document.getElementById('required-bar');
  var requiredLabel = document.getElementById('required-label');
  function updateRequired() {
    var missing = [];
    if (!currentSku()) missing.push('SKU');
    if (!whatInput.value.trim()) missing.push('“What is it?”');
    requiredBar.style.width = ((2 - missing.length) / 2 * 100) + '%';
    requiredLabel.textContent = missing.length ? 'Still needed: ' + missing.join(' and ') : 'Ready to save';
  }

  function syncDerivedUi() {
    statusSelect.setAttribute('data-status', statusSelect.value);
    updateRequired();
  }

  skuInput.addEventListener('input', function () {
    var pos = skuInput.selectionStart;
    var upper = skuInput.value.toUpperCase();
    if (upper !== skuInput.value) {
      skuInput.value = upper;
      try { skuInput.setSelectionRange(pos, pos); } catch (e) {}
    }
    skuInput.classList.remove('is-invalid');
  });
  statusSelect.addEventListener('change', syncDerivedUi);
  // Click a selected chip again to clear it.
  form.addEventListener('click', function (e) {
    var label = e.target.closest('label.choice');
    if (!label) return;
    var radio = label.querySelector('input[type="radio"]');
    if (radio && radio.checked && e.target !== radio) {
      e.preventDefault();
      radio.checked = false;
      form.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  form.addEventListener('input', updateRequired);
  form.addEventListener('change', updateRequired);

  /* ── 2. Autosave ─────────────────────────────────────────────────── */
  var autosaveEl = document.getElementById('autosave-state');
  var draftBanner = document.getElementById('draft-banner');
  var conflictBanner = document.getElementById('draft-conflict');
  var clientId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  var draftVersion = null;
  var draftSku = '';
  var dirty = false;
  var saving = false;
  var saveAgain = false;
  var submitting = false;
  var conflictData = null;
  var timer = null;

  function setAutosave(text, tone) {
    autosaveEl.hidden = !text;
    autosaveEl.textContent = text || '';
    autosaveEl.className = 'save-state' + (tone ? ' ' + tone : '');
  }

  function saveLocal() {
    try { localStorage.setItem(LOCAL_DRAFT, JSON.stringify(formData())); } catch (e) {}
  }

  function flush(options) {
    options = options || {};
    clearTimeout(timer);
    if (!dirty || submitting) return;
    var sku = currentSku();
    if (!sku) {
      saveLocal();
      dirty = false;
      setAutosave('Draft kept on this computer — add a SKU to save it for everyone', 'warn');
      return;
    }
    if (saving) { saveAgain = true; return; }
    if (sku !== draftSku) { draftVersion = null; draftSku = sku; }
    saving = true;
    dirty = false;
    setAutosave('Saving draft…', 'saving');
    P.api('/api/drafts/' + encodeURIComponent(sku) + '/', {
      method: 'POST',
      keepalive: !!options.keepalive,
      body: { data: formData(), version: draftVersion, client: clientId, force: !!options.force }
    }).then(function (resp) {
      draftVersion = resp.version;
      conflictBanner.hidden = true;
      try { localStorage.removeItem(LOCAL_DRAFT); } catch (e) {}
      setAutosave('Draft saved — press Save to keep it', 'saved');
    }).catch(function (err) {
      if (err.status === 409) {
        conflictData = err.data;
        conflictBanner.hidden = false;
        setAutosave('Not saved — see the message above', 'err');
        return;
      }
      dirty = true;
      setAutosave('Draft not saved (' + err.message + ')', 'err');
    }).finally(function () {
      saving = false;
      if (saveAgain) { saveAgain = false; flush(); }
    });
  }

  function markDirty() {
    if (submitting) return;
    dirty = true;
    clearTimeout(timer);
    timer = setTimeout(flush, 700);
  }
  form.addEventListener('input', function (e) { if (e.target.type !== 'file' && e.target.id !== 'copy-sku') markDirty(); });
  form.addEventListener('change', function (e) { if (e.target.type !== 'file' && e.target.id !== 'copy-sku' && e.target.id !== 'print-pink') markDirty(); });
  form.addEventListener('focusout', function (e) { if (e.target.name) flush(); });
  window.addEventListener('pagehide', function () { flush({ keepalive: true }); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flush({ keepalive: true });
  });

  document.getElementById('conflict-load').addEventListener('click', function () {
    if (conflictData && conflictData.data) applyData(conflictData.data);
    draftVersion = conflictData ? conflictData.version : draftVersion;
    conflictBanner.hidden = true;
    setAutosave('Loaded the newer changes', 'saved');
  });
  document.getElementById('conflict-keep').addEventListener('click', function () {
    conflictBanner.hidden = true;
    dirty = true;
    flush({ force: true });
  });
  document.getElementById('draft-discard').addEventListener('click', function () {
    var sku = currentSku();
    var go = function () { window.location.href = sku ? '/intake/?sku=' + encodeURIComponent(sku) : '/intake/?new=1'; };
    try { localStorage.removeItem(LOCAL_DRAFT); } catch (e) {}
    if (!sku) return go();
    P.api('/api/drafts/' + encodeURIComponent(sku) + '/', { method: 'DELETE' }).then(go).catch(go);
  });

  function loadDraftOnStart() {
    if (config.justSaved || config.hadErrors) {
      try { localStorage.removeItem(LOCAL_DRAFT); } catch (e) {}
      return;
    }
    if (config.clearDraft) {
      try {
        if (localStorage.getItem(LOCAL_BACKUP) && formLooksEmpty()) document.getElementById('restore-local').hidden = false;
      } catch (e) {}
      return;
    }
    if (config.sku) {
      P.api('/api/drafts/' + encodeURIComponent(config.sku) + '/').then(function (resp) {
        if (!resp.has_draft) return;
        applyData(resp.data);
        draftVersion = resp.version;
        draftSku = config.sku;
        var when = new Date(resp.updated_at);
        document.getElementById('draft-banner-text').textContent =
          'Showing unsaved changes from ' + when.toLocaleString() + '. Press Save to keep them.';
        draftBanner.hidden = false;
      }).catch(function () {});
      return;
    }
    try {
      var local = localStorage.getItem(LOCAL_DRAFT);
      if (local) {
        applyData(JSON.parse(local));
        document.getElementById('draft-banner-text').textContent = 'Restored what you were typing before.';
        draftBanner.hidden = false;
      }
    } catch (e) {}
  }
  document.getElementById('restore-local-btn').addEventListener('click', function () {
    try {
      var backup = localStorage.getItem(LOCAL_BACKUP);
      if (backup) { applyData(JSON.parse(backup)); markDirty(); }
    } catch (e) {}
    document.getElementById('restore-local').hidden = true;
    P.toast('Draft restored');
  });

  form.addEventListener('submit', function (e) {
    syncDerivedUi();
    var missingSku = !currentSku();
    var missingWhat = !whatInput.value.trim();
    skuInput.classList.toggle('is-invalid', missingSku);
    whatInput.classList.toggle('is-invalid', missingWhat);
    if (missingSku || missingWhat) {
      e.preventDefault();
      P.toast('Fill in the SKU and “What is it?” before saving.', 'err');
      (missingSku ? skuInput : whatInput).focus();
      return;
    }
    if (uploading) {
      e.preventDefault();
      P.toast('Wait for the photos to finish uploading, then save.', 'err');
      return;
    }
    submitting = true;
    clearTimeout(timer);
    skuInput.value = currentSku();
    try { localStorage.removeItem(LOCAL_DRAFT); } catch (err) {}
    document.getElementById('save-button').disabled = true;
  });
  form.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    }
    // Scanners end with Enter; don't let that submit the whole form from the SKU box.
    if (e.key === 'Enter' && e.target === skuInput) { e.preventDefault(); whatInput.focus(); }
  });

  /* ── 3. eBay category picker ─────────────────────────────────────── */
  (function () {
    var input = document.getElementById('ebay-category');
    var menu = document.getElementById('ebay-category-menu');
    var pathField = form.querySelector('[name="ebay_category_path"]');
    var idField = form.querySelector('[name="ebay_category_id"]');
    var pathHint = document.getElementById('ebay-category-path');
    var categories = null;
    var active = -1;
    var shown = [];

    function load() {
      if (categories) return Promise.resolve(categories);
      return P.api('/api/ebay-categories/').then(function (d) { categories = d.categories || []; return categories; })
        .catch(function () { categories = []; return categories; });
    }
    function close() { menu.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; }
    function choose(cat) {
      input.value = cat.name;
      pathField.value = cat.path;
      idField.value = cat.id;
      pathHint.textContent = cat.path;
      close();
      markDirty();
    }
    function render() {
      var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      shown = categories.filter(function (c) {
        var hay = (c.name + ' ' + c.path).toLowerCase();
        return words.every(function (w) { return hay.indexOf(w) !== -1; });
      }).slice(0, 40);
      if (!shown.length) { close(); return; }
      menu.innerHTML = shown.map(function (c, i) {
        return '<li role="option" data-index="' + i + '"' + (i === active ? ' class="is-active"' : '') + '>' +
          P.escapeHtml(c.name) + '<small>' + P.escapeHtml(c.path) + '</small></li>';
      }).join('');
      menu.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }
    input.addEventListener('focus', function () { load().then(render); });
    input.addEventListener('input', function () {
      pathField.value = '';
      idField.value = '';
      pathHint.textContent = '';
      active = -1;
      load().then(render);
    });
    input.addEventListener('keydown', function (e) {
      if (menu.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(shown.length - 1, active + 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(0, active - 1); render(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); choose(shown[active]); }
      else if (e.key === 'Escape') close();
    });
    menu.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li[data-index]');
      if (li) { e.preventDefault(); choose(shown[+li.getAttribute('data-index')]); }
    });
    input.addEventListener('blur', function () {
      setTimeout(close, 120);
      if (!pathField.value && categories) {
        var exact = categories.filter(function (c) { return c.name.toLowerCase() === input.value.trim().toLowerCase(); })[0];
        if (exact) choose(exact);
      }
    });
  })();

  /* ── 4. Copy fields from another SKU ─────────────────────────────── */
  (function () {
    var input = document.getElementById('copy-sku');
    var menu = document.getElementById('copy-sku-menu');
    var status = document.getElementById('copy-status');
    var suggestTimer = null;

    function copyFrom(sku) {
      sku = (sku || '').trim().toUpperCase();
      if (!sku) { status.textContent = 'Enter a SKU to copy from.'; return; }
      status.textContent = 'Loading ' + sku + '…';
      P.api('/api/items/' + encodeURIComponent(sku) + '/').then(function (resp) {
        applyData(resp.data, { skipSku: true });
        markDirty();
        status.textContent = 'Copied fields from ' + sku + '. The SKU and photos were left alone.';
        P.toast('Copied fields from ' + sku);
      }).catch(function (err) { status.textContent = err.message; });
    }
    input.addEventListener('input', function () {
      clearTimeout(suggestTimer);
      var q = input.value.trim();
      if (q.length < 2) { menu.hidden = true; return; }
      suggestTimer = setTimeout(function () {
        P.api('/api/suggestions/?q=' + encodeURIComponent(q)).then(function (d) {
          var results = (d.results || []).slice(0, 12);
          menu.innerHTML = results.map(function (r) {
            return '<li data-value="' + P.escapeHtml(r.value) + '">' + P.escapeHtml(r.label) + '</li>';
          }).join('');
          menu.hidden = !results.length;
        }).catch(function () { menu.hidden = true; });
      }, 180);
    });
    menu.addEventListener('mousedown', function (e) {
      var li = e.target.closest('li[data-value]');
      if (!li) return;
      e.preventDefault();
      input.value = li.getAttribute('data-value');
      menu.hidden = true;
      copyFrom(input.value);
    });
    input.addEventListener('blur', function () { setTimeout(function () { menu.hidden = true; }, 120); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); menu.hidden = true; copyFrom(input.value); }
    });
    document.getElementById('copy-sku-btn').addEventListener('click', function () { copyFrom(input.value); });
    var prefill = new URLSearchParams(window.location.search).get('copy_sku');
    if (prefill) { input.value = prefill.toUpperCase(); copyFrom(prefill); }
  })();

  /* ── 5. Photos ───────────────────────────────────────────────────── */
  var grid = document.getElementById('photo-grid');
  var photoInput = document.getElementById('photo-input');
  var dropzone = document.getElementById('dropzone');
  var photoHint = document.getElementById('photo-hint');
  var photoCount = document.getElementById('photo-count');
  var downloadBtn = document.getElementById('download-photos');
  var pending = [];
  var uploading = false;
  var gridSku = config.sku;

  function photoTile(photo) {
    var fig = document.createElement('figure');
    fig.className = 'photo';
    fig.style.margin = '0';
    fig.draggable = true;
    fig.setAttribute('data-photo-id', photo.id);
    fig.innerHTML = (photo.is_thumb ? '<span class="badge-thumb">Thumbnail</span>' : '') +
      (photo.low_res ? '<span class="badge-lowres" title="Small preview from the old app\'s export. Retake or replace it for listings.">Low-res</span>' : '') +
      '<img alt="" loading="lazy">' +
      '<div class="photo-actions">' +
        '<button type="button" data-photo-view title="View">' + P.icon('search') + '</button>' +
        '<button type="button" data-photo-thumb title="Use as thumbnail">' + P.icon('star') + '</button>' +
        '<button type="button" data-photo-delete title="Delete photo">' + P.icon('trash') + '</button>' +
      '</div>';
    fig.querySelector('img').src = photo.thumb_url;
    fig.querySelector('img').alt = photo.name;
    return fig;
  }

  function refreshCount() {
    var n = grid.querySelectorAll('.photo[data-photo-id]').length;
    photoCount.textContent = String(n);
    downloadBtn.hidden = n === 0;
  }

  function loadPhotos(sku) {
    gridSku = sku;
    if (!sku) { grid.innerHTML = ''; refreshCount(); return; }
    P.api('/api/photos/?sku=' + encodeURIComponent(sku)).then(function (d) {
      if (gridSku !== sku) return;
      grid.querySelectorAll('.photo[data-photo-id]').forEach(function (el) { el.remove(); });
      (d.photos || []).forEach(function (p) { grid.appendChild(photoTile(p)); });
      refreshCount();
    }).catch(function () {});
  }

  function shrink(file) {
    // Big phone photos are shrunk before upload so they send quickly over Wi-Fi.
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 2 * 1024 * 1024) return resolve(file);
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, 2000 / Math.max(img.width, img.height));
        if (scale >= 1) { URL.revokeObjectURL(url); return resolve(file); }
        var canvas = document.createElement('canvas');
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
          URL.revokeObjectURL(url);
          resolve(blob ? new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }) : file);
        }, 'image/jpeg', 0.88);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  function addFiles(fileList) {
    Array.prototype.forEach.call(fileList || [], function (file) {
      if (!/^image\//.test(file.type)) { P.toast(file.name + ' is not an image.', 'err'); return; }
      if (file.size > config.photoLimitBytes * 4) { P.toast(file.name + ' is too large.', 'err'); return; }
      var preview = document.createElement('figure');
      preview.className = 'photo uploading';
      preview.style.margin = '0';
      preview.setAttribute('data-progress', currentSku() ? 'Queued' : 'Waiting for SKU');
      var img = document.createElement('img');
      img.src = URL.createObjectURL(file);
      preview.appendChild(img);
      grid.appendChild(preview);
      pending.push({ file: file, el: preview });
    });
    processQueue();
  }

  function processQueue() {
    if (uploading || !pending.length) return;
    var sku = currentSku();
    if (!sku) {
      photoHint.textContent = 'Enter a SKU and the queued photos will upload automatically.';
      skuInput.focus();
      return;
    }
    uploading = true;
    var entry = pending.shift();
    entry.el.setAttribute('data-progress', 'Uploading…');
    shrink(entry.file).then(function (file) {
      var fd = new FormData();
      fd.append('sku', sku);
      fd.append('photo', file, file.name);
      return P.upload('/api/photos/upload/', fd, function (pct) { entry.el.setAttribute('data-progress', pct + '%'); });
    }).then(function (resp) {
      var photo = resp.photos[0];
      var tile = photoTile(photo);
      entry.el.replaceWith(tile);
      refreshCount();
      photoHint.textContent = 'Drag to reorder. The starred photo is used everywhere as the thumbnail.';
    }).catch(function (err) {
      entry.el.remove();
      P.toast(err.message, 'err');
    }).finally(function () {
      uploading = false;
      processQueue();
    });
  }

  dropzone.addEventListener('click', function () { photoInput.click(); });
  dropzone.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); photoInput.click(); }
  });
  photoInput.addEventListener('change', function () { addFiles(photoInput.files); photoInput.value = ''; });
  ['dragenter', 'dragover'].forEach(function (name) {
    dropzone.addEventListener(name, function (e) {
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') !== -1) {
        e.preventDefault();
        dropzone.classList.add('is-hover');
      }
    });
  });
  ['dragleave', 'drop'].forEach(function (name) {
    dropzone.addEventListener(name, function () { dropzone.classList.remove('is-hover'); });
  });
  dropzone.addEventListener('drop', function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); }
  });
  document.addEventListener('paste', function (e) {
    var files = e.clipboardData && e.clipboardData.files;
    if (files && files.length && !e.target.closest('input[type="text"], textarea')) { e.preventDefault(); addFiles(files); }
  });

  // Photos follow the SKU: typing a SKU shows its photos and uploads anything queued.
  var skuTimer = null;
  skuInput.addEventListener('input', function () {
    clearTimeout(skuTimer);
    skuTimer = setTimeout(function () {
      var sku = currentSku();
      if (sku !== gridSku) loadPhotos(sku);
      updateSkuLinks(sku);
      pending.forEach(function (p) { p.el.setAttribute('data-progress', sku ? 'Queued' : 'Waiting for SKU'); });
      processQueue();
    }, 400);
  });

  grid.addEventListener('click', function (e) {
    var tile = e.target.closest('.photo[data-photo-id]');
    if (!tile) return;
    var id = tile.getAttribute('data-photo-id');
    if (e.target.closest('[data-photo-view]')) {
      P.lightbox('/photos/' + id + '/');
    } else if (e.target.closest('[data-photo-thumb]')) {
      P.api('/api/photos/' + id + '/thumbnail/', { method: 'POST', body: {} }).then(function () {
        grid.querySelectorAll('.badge-thumb').forEach(function (b) { b.remove(); });
        var badge = document.createElement('span');
        badge.className = 'badge-thumb';
        badge.textContent = 'Thumbnail';
        tile.prepend(badge);
        P.toast('Thumbnail updated');
      }).catch(function (err) { P.toast(err.message, 'err'); });
    } else if (e.target.closest('[data-photo-delete]')) {
      P.confirm({ title: 'Delete this photo?', body: 'The photo file is removed for good.', confirmLabel: 'Delete', danger: true })
        .then(function (yes) {
          if (!yes) return;
          P.api('/api/photos/' + id + '/delete/', { method: 'POST', body: {} })
            .then(function () { tile.remove(); refreshCount(); P.toast('Photo deleted'); })
            .catch(function (err) { P.toast(err.message, 'err'); });
        });
    }
  });

  // Drag to reorder
  var dragged = null;
  grid.addEventListener('dragstart', function (e) {
    dragged = e.target.closest('.photo[data-photo-id]');
    if (!dragged) return;
    dragged.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.getAttribute('data-photo-id'));
  });
  grid.addEventListener('dragover', function (e) {
    var over = e.target.closest('.photo[data-photo-id]');
    if (!dragged || !over || over === dragged) return;
    e.preventDefault();
    grid.querySelectorAll('.drop-target').forEach(function (el) { el.classList.remove('drop-target'); });
    over.classList.add('drop-target');
  });
  grid.addEventListener('drop', function (e) {
    var over = e.target.closest('.photo[data-photo-id]');
    if (e.dataTransfer.files && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); return; }
    if (!dragged || !over || over === dragged) return;
    e.preventDefault();
    var rect = over.getBoundingClientRect();
    var after = (e.clientX - rect.left) > rect.width / 2;
    over.parentNode.insertBefore(dragged, after ? over.nextSibling : over);
    var ids = Array.prototype.map.call(grid.querySelectorAll('.photo[data-photo-id]'), function (el) { return +el.getAttribute('data-photo-id'); });
    P.api('/api/photos/reorder/', { method: 'POST', body: { ids: ids } }).catch(function (err) { P.toast(err.message, 'err'); });
  });
  grid.addEventListener('dragend', function () {
    if (dragged) dragged.classList.remove('is-dragging');
    grid.querySelectorAll('.drop-target').forEach(function (el) { el.classList.remove('drop-target'); });
    dragged = null;
  });

  downloadBtn.addEventListener('click', function () {
    var ids = Array.prototype.map.call(grid.querySelectorAll('.photo[data-photo-id]'), function (el) { return el.getAttribute('data-photo-id'); });
    ids.forEach(function (id, index) {
      setTimeout(function () {
        var a = document.createElement('a');
        a.href = '/photos/' + id + '/?download=1';
        a.download = '';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }, index * 350);
    });
  });

  /* ── 6. QR, printing, sticker, delete, quick mode ────────────────── */
  var qrBox = document.getElementById('intake-qr');
  var qrPanel = document.getElementById('qr-panel');
  function renderQr(target, sku, size) {
    if (!window.QRCode || !target) return;
    target.innerHTML = '';
    if (!sku) return;
    new QRCode(target, { text: config.cardUrlBase + encodeURIComponent(sku) + '/', width: size, height: size,
      colorDark: '#1d2427', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.H });
  }
  function updateSkuLinks(sku) {
    qrPanel.hidden = !sku;
    // Drawn large and scaled down by CSS so it stays sharp at any panel width.
    renderQr(qrBox, sku, 320);
    var phoneLink = document.getElementById('open-phone-card');
    if (phoneLink) phoneLink.href = '/m/' + encodeURIComponent(sku) + '/';
    document.getElementById('open-script').href = '/scripts/?sku=' + encodeURIComponent(sku);
    var imagesLink = document.getElementById('open-images');  // absent while Listing images is turned off
    if (imagesLink) imagesLink.href = '/listing-images/?sku=' + encodeURIComponent(sku);
  }
  updateSkuLinks(currentSku());

  var pinkBox = document.getElementById('print-pink');
  try { pinkBox.checked = localStorage.getItem('pinksheet:print-pink') === '1'; } catch (e) {}
  pinkBox.addEventListener('change', function () {
    try { localStorage.setItem('pinksheet:print-pink', pinkBox.checked ? '1' : '0'); } catch (e) {}
  });

  function labelFor(input) {
    var id = input.id && form.querySelector('label[for="' + input.id + '"]');
    if (id) return id.textContent.trim();
    var prop = input.closest('.prop');
    return prop ? prop.querySelector('.prop-name').textContent.trim() : input.name;
  }
  function fillPrintList(target, section) {
    var dl = document.querySelector('[data-print-list="' + target + '"]');
    dl.innerHTML = '';
    section.querySelectorAll('.prop').forEach(function (prop) {
      var name = prop.querySelector('.prop-name').textContent.trim();
      var values = [];
      prop.querySelectorAll('input, select, textarea').forEach(function (field) {
        if (field.type === 'hidden') return;
        if (field.type === 'radio') { if (field.checked) values.push(field.value); }
        else if (field.type === 'checkbox') { if (field.checked) values.push(field.parentNode.textContent.trim()); }
        else if (field.value) values.push(field.value);
      });
      var dt = document.createElement('dt');
      dt.textContent = name;
      var dd = document.createElement('dd');
      dd.textContent = values.join(', ') || '—';
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
  }
  function preparePrint() {
    var data = formData();
    document.body.classList.toggle('print-pink', pinkBox.checked);
    var set = function (name, value) {
      document.querySelectorAll('[data-print="' + name + '"]').forEach(function (el) { el.textContent = value || '—'; });
    };
    set('sku', currentSku());
    set('status', statusSelect.options[statusSelect.selectedIndex].text);
    // ACTIVE / INACTIVE / SOLD, as the board shows it. Same rule as saving: the Sold lane means SOLD,
    // and leaving Sold drops back to INACTIVE.
    var review = document.getElementById('print-review');
    if (review) {
      var saved = Number(review.getAttribute('data-reviewed'));
      review.textContent = statusSelect.value === 'sold' ? 'SOLD' : saved === 1 ? 'ACTIVE' : 'INACTIVE';
    }
    set('price', data.price ? '$' + Number(data.price).toFixed(2) : '');
    set('quantity', data.quantity);
    set('what_is_it', data.what_is_it);
    set('notes', data.notes);
    set('printed_at', new Date().toLocaleString());
    fillPrintList('d1', document.querySelector('[data-print-section="d1"]'));
    fillPrintList('d2', document.querySelector('[data-print-section="d2"]'));
    var details = document.querySelector('[data-print-list="details"]');
    details.innerHTML = '';
    [['eBay category', data.ebay_category], ['Location', data.where_it_goes], ['Date received', data.date_received], ['Came from', data.source]]
      .forEach(function (pair) {
        var dt = document.createElement('dt'); dt.textContent = pair[0];
        var dd = document.createElement('dd'); dd.textContent = pair[1] || '—';
        details.appendChild(dt); details.appendChild(dd);
      });
    renderQr(document.getElementById('print-qr'), currentSku(), 110);
    var gallery = document.getElementById('print-gallery');
    gallery.innerHTML = '';
    var thumbId = (document.querySelector('.print-thumb img') || {}).src || '';
    Array.prototype.slice.call(grid.querySelectorAll('.photo[data-photo-id]')).filter(function (el) {
      return thumbId.indexOf('/photos/' + el.getAttribute('data-photo-id') + '/') === -1;
    }).slice(0, 4).forEach(function (el) {
      var img = document.createElement('img');
      img.src = '/photos/' + el.getAttribute('data-photo-id') + '/';
      gallery.appendChild(img);
    });
  }
  window.addEventListener('beforeprint', preparePrint);
  var printButton = document.getElementById('print-sheet-btn');
  if (printButton) {
    printButton.addEventListener('click', function () {
      preparePrint();
      var images = Array.prototype.slice.call(document.querySelectorAll('.print-sheet img'));
      var waiting = images.filter(function (img) { return !img.complete; });
      var printed = false;
      var go = function () {
        if (printed) return;
        printed = true;
        setTimeout(function () { window.print(); }, 100);
      };
      if (!waiting.length) return go();
      var left = waiting.length;
      var done = function () { left -= 1; if (left === 0) go(); };
      waiting.forEach(function (img) { img.addEventListener('load', done, { once: true }); img.addEventListener('error', done, { once: true }); });
      setTimeout(go, 2500);
    });
  }

  var stickerBtn = document.getElementById('print-sticker');
  stickerBtn.addEventListener('click', function () {
    if (window.PinksheetLabels) window.PinksheetLabels.print(currentSku(), stickerBtn.getAttribute('data-label-preset'), stickerBtn);
  });

  var deleteBtn = document.getElementById('delete-item');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', function () {
      P.confirm({
        title: 'Delete ' + currentSku() + '?',
        body: 'The item disappears from the board and lookup. You can undo right after. Photos are kept.',
        confirmLabel: 'Delete item', danger: true, typeToConfirm: 'DELETE'
      }).then(function (yes) {
        if (!yes) return;
        P.api('/api/items/' + deleteBtn.getAttribute('data-id') + '/delete/', { method: 'POST', body: { confirm: 'DELETE' } })
          .then(function () {
            submitting = true;
            P.api('/api/drafts/' + encodeURIComponent(currentSku()) + '/', { method: 'DELETE' }).catch(function () {});
            var leave = setTimeout(function () { window.location.href = '/lookup/'; }, 6500);
            P.toast('Deleted ' + currentSku(), 'ok', {
              label: 'Undo',
              onClick: function () {
                clearTimeout(leave);
                P.api('/api/items/undo-delete/', { method: 'POST', body: {} })
                  .then(function () { window.location.reload(); })
                  .catch(function (err) { P.toast(err.message, 'err'); });
              }
            });
          })
          .catch(function (err) { P.toast(err.message, 'err'); });
      });
    });
  }

  var quickToggle = document.getElementById('quick-mode-toggle');
  function applyQuick(on) {
    document.body.classList.toggle('quick-mode', on);
    quickToggle.textContent = on ? 'Show everything' : 'Quick mode';
  }
  try { applyQuick(localStorage.getItem('pinksheet:quick-mode') === '1'); } catch (e) {}
  quickToggle.addEventListener('click', function () {
    var on = !document.body.classList.contains('quick-mode');
    applyQuick(on);
    try { localStorage.setItem('pinksheet:quick-mode', on ? '1' : '0'); } catch (e) {}
  });

  syncDerivedUi();
  loadDraftOnStart();
})();
