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
})();
