/* Imaging page: upload .json files, confirm removals, and refresh while computers are imaging. */
(function () {
  'use strict';
  var P = window.Pinksheet;

  var upload = document.querySelector('[data-imaging-upload]');
  if (upload) {
    upload.querySelector('input[type="file"]').addEventListener('change', function () {
      if (this.files.length) upload.submit();
    });
  }

  document.addEventListener('submit', function (e) {
    var form = e.target.closest('[data-imaging-remove]');
    if (!form || form.dataset.confirmed) return;
    e.preventDefault();
    P.confirm({
      title: 'Remove ' + form.dataset.serial + '?',
      body: 'It disappears from this page. Items are not changed, and it comes back if the imaging app reports it again.',
      confirmLabel: 'Remove', danger: true
    }).then(function (ok) {
      if (!ok) return;
      form.dataset.confirmed = '1';
      form.submit();
    });
  });

  // While anything is still imaging, refresh the list every 10 seconds,
  // unless someone is typing a SKU or has details open.
  var holder = document.querySelector('[data-imaging-list]');
  function busy() {
    return holder.contains(document.activeElement) && document.activeElement.matches('input') ||
      holder.querySelector('details[open]');
  }
  function refresh() {
    var list = holder.querySelector('.imaging-list');
    if (!list || !list.dataset.inProgress || document.hidden || busy()) return;
    fetch(window.location.href, { headers: { 'X-Refresh': '1' }, credentials: 'same-origin' })
      .then(function (resp) { return resp.ok ? resp.text() : null; })
      .then(function (html) { if (html && !busy()) holder.innerHTML = html; })
      .catch(function () {});
  }
  if (holder) {
    setInterval(refresh, 10000);
    document.addEventListener('visibilitychange', refresh);
  }
})();
