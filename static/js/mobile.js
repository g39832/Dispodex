/* Phone card: quick edits and camera uploads for the item a QR code points at. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var sku = JSON.parse(document.getElementById('mobile-sku').textContent);
  var stateEl = document.getElementById('m-state');

  function show(text, tone) {
    stateEl.hidden = false;
    stateEl.textContent = text;
    stateEl.className = 'save-state ' + (tone || '');
  }
  function save(field, value) {
    show('Saving…', 'saving');
    return P.api('/api/items/' + encodeURIComponent(sku) + '/update/', { method: 'POST', keepalive: true, body: { field: field, value: value } })
      .then(function (resp) { show('Saved', 'saved'); return resp; })
      .catch(function (err) { show('Not saved: ' + err.message, 'err'); throw err; });
  }

  var status = document.getElementById('m-status');
  status.addEventListener('change', function () {
    save('status', status.value).then(function (resp) {
      var tag = document.getElementById('status-tag');
      tag.dataset.status = resp.item.status;
      tag.textContent = resp.item.status_label;
    }).catch(function () {});
  });
  var price = document.getElementById('m-price');
  price.addEventListener('change', function () { save('price', price.value).catch(function () {}); });

  // Notes save while typing: a phone can drop the page (a call, the lock screen) before "blur" fires.
  var notes = document.getElementById('m-notes');
  var notesTimer = null;
  notes.addEventListener('input', function () {
    clearTimeout(notesTimer);
    notesTimer = setTimeout(function () { notesTimer = null; save('notes', notes.value).catch(function () {}); }, 800);
  });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && notesTimer) {
      clearTimeout(notesTimer);
      notesTimer = null;
      save('notes', notes.value).catch(function () {});
    }
  });

  document.getElementById('m-sold').addEventListener('click', function (e) {
    var btn = e.currentTarget;
    save('status', 'sold').then(function () {
      btn.disabled = true;
      setTimeout(function () { location.reload(); }, 700);
    }).catch(function () {});
  });

  var uploadState = document.getElementById('m-upload-state');
  var queue = [];
  var busy = false;
  function next() {
    if (!queue.length) {
      busy = false;
      uploadState.textContent = 'All photos uploaded.';
      setTimeout(function () { location.reload(); }, 900);
      return;
    }
    busy = true;
    var file = queue.shift();
    uploadState.textContent = 'Preparing photo… (' + (queue.length + 1) + ' left)';
    var send = function (blob) {
      var fd = new FormData();
      fd.append('sku', sku);
      fd.append('photo', blob, blob === file && file.name ? file.name : (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg');
      P.upload('/api/photos/upload/', fd, function (pct) { uploadState.textContent = 'Uploading… ' + pct + '%'; })
        .then(next)
        .catch(function (err) {
          busy = false;
          queue = [];
          uploadState.textContent = 'Upload failed: ' + err.message;
        });
    };
    // Send the camera's own file so the server resizes it at full quality. Only a file too big to
    // upload, or one in a format the server can't read, is converted here at near-lossless quality.
    var readable = /^image\/(jpeg|png|webp|gif|heic|heif)$/.test(file.type);
    if (window.Compressor && (!readable || file.size > 30 * 1024 * 1024)) {
      new Compressor(file, {
        quality: 0.95, maxWidth: 3000, maxHeight: 3000, convertSize: 0,
        success: send, error: function () { send(file); }
      });
    } else {
      send(file);
    }
  }
  function addFiles(files) {
    Array.prototype.forEach.call(files || [], function (f) { queue.push(f); });
    if (!busy) next();
  }
  [['m-camera', 'm-camera-input'], ['m-gallery', 'm-gallery-input']].forEach(function (pair) {
    var input = document.getElementById(pair[1]);
    document.getElementById(pair[0]).addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () { addFiles(input.files); input.value = ''; });
  });
})();
