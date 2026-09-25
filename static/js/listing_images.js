/* eBay listing-image composer: position images on a canvas; the layout saves per SKU. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var sku = JSON.parse(document.getElementById('composer-sku').textContent || '""');
  var canvas = document.getElementById('canvas');
  if (!sku || !canvas) return;
  var images = JSON.parse(document.getElementById('composer-positions').textContent || '[]') || [];
  var empty = document.getElementById('canvas-empty');
  var state = document.getElementById('composer-state');
  var saveTimer = null;

  function setState(text, tone) { state.textContent = text; state.className = 'save-state ' + (tone || ''); }

  function sendLayout(keepalive) {
    saveTimer = null;
    return P.api('/api/listing-images/' + encodeURIComponent(sku) + '/layout/', {
      method: 'POST', keepalive: !!keepalive, body: { positions: images }
    }).then(function () { setState('Layout saved', 'saved'); })
      .catch(function (err) { setState('Not saved: ' + err.message, 'err'); });
  }
  function saveNow() {
    clearTimeout(saveTimer);
    setState('Saving…', 'saving');
    return sendLayout(false);
  }
  function save() {
    clearTimeout(saveTimer);
    setState('Saving…', 'saving');
    saveTimer = setTimeout(sendLayout, 300);
  }
  // Leaving the page right after a change must not lose it.
  window.addEventListener('pagehide', function () {
    if (saveTimer) { clearTimeout(saveTimer); sendLayout(true); }
  });

  function render() {
    canvas.querySelectorAll('.canvas-img').forEach(function (el) { el.remove(); });
    empty.hidden = images.length > 0;
    images.forEach(function (img, index) {
      var box = document.createElement('div');
      box.className = 'canvas-img';
      box.style.left = (img.x || 0) + 'px';
      box.style.top = (img.y || 0) + 'px';
      var pic = document.createElement('img');
      pic.src = img.src;
      pic.alt = img.name || '';
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.title = 'Remove';
      remove.innerHTML = P.icon('x');
      remove.addEventListener('click', function (e) { e.stopPropagation(); images.splice(index, 1); render(); saveNow(); });
      box.appendChild(pic);
      box.appendChild(remove);
      canvas.appendChild(box);
      makeDraggable(box, index);
    });
  }

  function makeDraggable(box, index) {
    box.addEventListener('pointerdown', function (e) {
      if (e.target.closest('button')) return;
      e.preventDefault();
      box.setPointerCapture(e.pointerId);
      var rect = canvas.getBoundingClientRect();
      var startX = e.clientX, startY = e.clientY;
      var originX = images[index].x || 0, originY = images[index].y || 0;
      box.classList.add('dragging');
      var move = function (ev) {
        var x = Math.max(0, Math.min(rect.width - box.offsetWidth, originX + ev.clientX - startX));
        var y = Math.max(0, Math.min(rect.height - box.offsetHeight, originY + ev.clientY - startY));
        box.style.left = x + 'px';
        box.style.top = y + 'px';
        images[index].x = Math.round(x);
        images[index].y = Math.round(y);
      };
      var up = function () {
        box.classList.remove('dragging');
        box.removeEventListener('pointermove', move);
        box.removeEventListener('pointerup', up);
        save();
      };
      box.addEventListener('pointermove', move);
      box.addEventListener('pointerup', up);
    });
  }

  function add(src, name, x, y) {
    images.push({ id: Date.now() + Math.random(), src: src, name: name || 'Image', x: Math.round(x || 30), y: Math.round(y || 30) });
    render();
    saveNow();
  }

  function uploadFile(file, x, y) {
    var fd = new FormData();
    fd.append('sku', sku);
    fd.append('photo', file, file.name);
    setState('Uploading ' + file.name + '…', 'saving');
    P.upload('/api/listing-images/upload/', fd, function (pct) { setState('Uploading… ' + pct + '%', 'saving'); })
      .then(function (d) { add(d.url, d.name, x, y); })
      .catch(function (err) { setState(err.message, 'err'); P.toast(err.message, 'err'); });
  }

  var strip = document.getElementById('strip');
  if (strip) {
    strip.addEventListener('dragstart', function (e) {
      var img = e.target.closest('img[data-src]');
      if (img) e.dataTransfer.setData('text/plain', img.getAttribute('data-src'));
    });
  }
  canvas.addEventListener('dragover', function (e) { e.preventDefault(); canvas.classList.add('drag-over'); });
  canvas.addEventListener('dragleave', function (e) { if (!canvas.contains(e.relatedTarget)) canvas.classList.remove('drag-over'); });
  canvas.addEventListener('drop', function (e) {
    e.preventDefault();
    canvas.classList.remove('drag-over');
    var rect = canvas.getBoundingClientRect();
    var x = Math.max(0, e.clientX - rect.left - 90);
    var y = Math.max(0, e.clientY - rect.top - 67);
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      Array.prototype.forEach.call(e.dataTransfer.files, function (f) { if (/^image\//.test(f.type)) uploadFile(f, x, y); });
      return;
    }
    var src = e.dataTransfer.getData('text/plain');
    if (src) add(src, 'Photo', x, y);
  });

  var drop = document.getElementById('composer-drop');
  var fileInput = document.getElementById('composer-file');
  drop.addEventListener('click', function () { fileInput.click(); });
  fileInput.addEventListener('change', function () {
    Array.prototype.forEach.call(fileInput.files, function (f) { uploadFile(f); });
    fileInput.value = '';
  });
  document.getElementById('composer-url-add').addEventListener('click', function () {
    var input = document.getElementById('composer-url');
    var url = input.value.trim();
    if (!/^https?:\/\//i.test(url)) { P.toast('Paste a full image link starting with https://', 'err'); return; }
    add(url, 'Linked image', 40 + Math.random() * 60, 40 + Math.random() * 60);
    input.value = '';
  });
  document.getElementById('composer-clear').addEventListener('click', function () {
    P.confirm({ title: 'Clear the canvas?', body: 'Uploaded files stay on the server; only the layout is cleared.', confirmLabel: 'Clear', danger: true })
      .then(function (yes) { if (yes) { images = []; render(); saveNow(); } });
  });

  render();
})();
