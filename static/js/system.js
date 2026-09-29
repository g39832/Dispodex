/* System page: Square test / queue buttons. (Backup buttons come from dashboard.js.) */
(function () {
  'use strict';
  var P = window.Pinksheet;

  function wire(id, url, onSuccess) {
    var btn = document.getElementById(id);
    if (!btn) return;
    btn.addEventListener('click', function () {
      var label = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Working…';
      fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': P.csrfToken } })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (res.ok && res.data.ok !== false) onSuccess(res.data);
          else P.toast(res.data.message || res.data.error || 'That did not work.', 'err');
        })
        .catch(function () { P.toast('The server did not answer.', 'err'); })
        .finally(function () { btn.disabled = false; btn.textContent = label; });
    });
  }

  wire('square-test', '/api/square/test/', function (d) { P.toast(d.message); });
  wire('square-queue-all', '/api/square/queue-all/', function (d) { P.toast(d.queued + ' items queued for Square'); });
  wire('square-retry', '/api/square/retry/', function (d) {
    P.toast(d.reset + ' job(s) will be retried');
    setTimeout(function () { location.reload(); }, 900);
  });

  /* Import database: pick a file, confirm, upload. */
  var importBtn = document.getElementById('import-database');
  var importFile = document.getElementById('import-database-file');
  if (importBtn && importFile) {
    importBtn.addEventListener('click', function () { importFile.click(); });
    importFile.addEventListener('change', function () {
      var file = importFile.files[0];
      importFile.value = '';
      if (!file) return;
      P.confirm({
        title: 'Import ' + file.name + '?',
        body: 'A Dispodex database replaces all current items, photos list, history and settings with the ones in this file. ' +
          'An old Pinksheet database replaces the current items with its items. A backup is made first so this can be undone.',
        confirmLabel: 'Import',
        danger: true,
        typeToConfirm: 'IMPORT'
      }).then(function (ok) {
        if (!ok) return;
        var label = importBtn.innerHTML;
        importBtn.disabled = true;
        importBtn.textContent = 'Importing…';
        var form = new FormData();
        form.append('file', file);
        P.api('/api/ops/import-database/', { method: 'POST', form: form })
          .then(function (d) {
            P.toast('Imported ' + d.items + ' items. Previous data saved as ' + d.backup);
            setTimeout(function () { location.reload(); }, 1800);
          })
          .catch(function (err) {
            P.toast(err.message, 'err');
            importBtn.disabled = false;
            importBtn.innerHTML = label;
          });
      });
    });
  }
})();
