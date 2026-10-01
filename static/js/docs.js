/* Team docs: list filters, preview picked photos before posting, and confirm before deleting a post. */
(function () {
  'use strict';
  var P = window.Pinksheet;

  // "Posted by" applies as soon as it changes, like the category chips.
  document.querySelectorAll('[data-doc-filters] [data-autosubmit]').forEach(function (select) {
    select.addEventListener('change', function () { select.form.submit(); });
  });

  var picker = document.querySelector('[data-doc-photos]');
  var preview = document.querySelector('[data-doc-preview]');
  var urls = [];
  if (picker && preview) {
    picker.addEventListener('change', function () {
      urls.forEach(function (url) { URL.revokeObjectURL(url); });
      urls = [];
      preview.innerHTML = '';
      Array.prototype.forEach.call(picker.files, function (file) {
        if (!/^image\//.test(file.type)) return;
        var img = document.createElement('img');
        var url = URL.createObjectURL(file);
        urls.push(url);
        img.src = url;
        img.alt = file.name;
        var tile = document.createElement('div');
        tile.className = 'doc-photo';
        tile.appendChild(img);
        preview.appendChild(tile);
      });
      preview.hidden = !preview.children.length;
    });
  }

  var form = document.querySelector('[data-doc-form]');
  if (form) {
    form.addEventListener('submit', function () {
      var button = form.querySelector('button[type="submit"]');
      if (button) { button.disabled = true; button.lastChild.textContent = ' Saving…'; }
    });
  }

  document.querySelectorAll('[data-doc-delete]').forEach(function (del) {
    del.addEventListener('submit', function (e) {
      if (del.dataset.confirmed) return;
      e.preventDefault();
      P.confirm({
        title: 'Delete this post?',
        body: '“' + del.dataset.title + '” and its photos are removed for everyone.',
        confirmLabel: 'Delete', danger: true
      }).then(function (ok) {
        if (!ok) return;
        del.dataset.confirmed = '1';
        del.submit();
      });
    });
  });
})();
