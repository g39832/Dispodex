/* Dashboard: backup buttons, Square status and reconciliation card. */
(function () {
  'use strict';
  var P = window.Pinksheet;

  function busy(button, label) {
    var original = button.innerHTML;
    button.disabled = true;
    button.textContent = label;
    return function () { button.disabled = false; button.innerHTML = original; };
  }

  document.addEventListener('click', function (e) {
    var backupBtn = e.target.closest('[data-run-backup]');
    if (backupBtn) {
      var done = busy(backupBtn, 'Backing up…');
      P.api('/api/ops/backup/', { method: 'POST', body: {} })
        .then(function (data) { P.toast('Backup finished: ' + (data.file || 'done')); setTimeout(function () { location.reload(); }, 900); })
        .catch(function (err) { P.toast('Backup failed: ' + err.message, 'err'); })
        .finally(done);
    }
    var verifyBtn = e.target.closest('[data-verify-backup]');
    if (verifyBtn) {
      var doneVerify = busy(verifyBtn, 'Verifying…');
      P.api('/api/ops/verify/', { method: 'POST', body: {} })
        .then(function (data) { P.toast((data.messages || ['Backup verified']).join(' · ')); })
        .catch(function (err) { P.toast('Verify failed: ' + err.message, 'err'); })
        .finally(doneVerify);
    }
    var syncBtn = e.target.closest('[data-square-sync]');
    if (syncBtn && !syncBtn.disabled) {
      syncBtn.disabled = true;
      P.api('/api/square/sync-all/', { method: 'POST', body: {} })
        .then(function (data) {
          if (!data.started) P.toast('A Square sync is already running (' + countText(data) + ')' + byWhom(data) + '.');
          showSyncProgress(data);
          followSync();
        })
        .catch(function (err) { syncBtn.disabled = false; P.toast(err.message, 'err'); });
    }
    var reconBtn = e.target.closest('[data-recon-run]');
    if (reconBtn) {
      var doneRecon = busy(reconBtn, 'Checking…');
      var form = new FormData();
      P.api('/api/reconciliation/run/', { method: 'POST', form: form })
        .then(function (data) { P.toast('Reconciliation: ' + data.message); refreshRecon(); })
        .catch(function (err) { P.toast(err.message, 'err'); })
        .finally(doneRecon);
    }
  });

  /* "Sync Square now" runs on the server in the background; follow its progress. */
  var syncButton = document.querySelector('[data-square-sync]');
  var syncLabel = syncButton ? syncButton.innerHTML : '';
  var following = null;
  function countText(d) { return (d.done || 0).toLocaleString() + ' of ' + (d.total || 0).toLocaleString(); }
  function byWhom(d) { return d.started_by ? ', started by ' + d.started_by : ''; }
  function showSyncProgress(d) {
    if (!syncButton) return;
    if (d.running) {
      syncButton.disabled = true;
      syncButton.textContent = 'Syncing… ' + countText(d);
    } else {
      syncButton.disabled = false;
      syncButton.innerHTML = syncLabel;
    }
  }
  function followSync() {
    if (following) return;
    following = setInterval(function () {
      P.api('/api/square/sync-all/progress/').then(function (d) {
        showSyncProgress(d);
        if (d.running) return;
        clearInterval(following);
        following = null;
        if (!d.total) return;
        var msg = 'Square sync finished: ' + d.updated + ' updated, ' + d.skipped + ' unchanged';
        if (d.error) {
          // The full error is on the System page; keep the message readable.
          var first = d.errors[0].message.length > 120 ? d.errors[0].message.slice(0, 120) + '…' : d.errors[0].message;
          msg += ', ' + d.error + ' failed (first: ' + d.errors[0].sku + ' — ' + first + ')';
        }
        P.toast(msg, d.error ? 'err' : 'ok');
        refreshSquare();
      }).catch(function () {});
    }, 2000);
  }
  if (syncButton) {
    // A sync started earlier (or by someone else) is shown as soon as the page opens.
    P.api('/api/square/sync-all/progress/').then(function (d) {
      if (d.running) { showSyncProgress(d); followSync(); }
    }).catch(function () {});
  }

  function setText(selector, text) {
    var el = document.querySelector(selector);
    if (el) el.textContent = text;
  }

  function refreshSquare() {
    var card = document.getElementById('square-card');
    if (!card) return;
    P.api('/api/square/status/').then(function (d) {
      var state = card.querySelector('[data-square-state]');
      state.className = 'tag ' + (d.connected ? 'green' : '');
      state.textContent = d.connected ? 'Connected · ' + d.environment : 'Not set up';
      card.querySelector('[data-square-setup]').hidden = d.connected;
      setText('[data-square-last]', d.last_sync_at || (d.connected ? 'Not synced yet' : '—'));
      var queue = d.queue_waiting + ' waiting';
      if (d.queue_dead_letter) queue += ' · ' + d.queue_dead_letter + ' gave up';
      setText('[data-square-queue]', queue);
      setText('[data-square-sales]', String(d.sold_today || 0));
      setText('[data-square-recent]', d.recent_sale ? d.recent_sale.sku + ' · $' + d.recent_sale.price : '—');
      var errorBox = card.querySelector('[data-square-error]');
      errorBox.hidden = !(d.connected && d.last_error);
      errorBox.textContent = d.last_error ? 'Last error: ' + d.last_error : '';
    }).catch(function () { setText('[data-square-state]', 'Offline'); });
  }

  function refreshRecon() {
    var card = document.getElementById('recon-card');
    if (!card) return;
    P.api('/api/reconciliation/').then(function (d) {
      var state = card.querySelector('[data-recon-state]');
      var list = card.querySelector('[data-recon-issues]');
      list.innerHTML = '';
      if (d.is_running) {
        state.className = 'tag blue'; state.textContent = 'Running';
        return;
      }
      if (!d.last_run) {
        state.className = 'tag'; state.textContent = 'Never run';
        return;
      }
      var run = d.last_run;
      var failed = run.status === 'failed';
      state.className = 'tag ' + (failed ? 'red' : (d.pending_issues ? 'amber' : 'green'));
      state.textContent = failed ? 'Failed' : (d.pending_issues ? d.pending_issues + ' open' : 'Healthy');
      setText('[data-recon-summary]', 'Last run ' + (run.completed_at || run.started_at) + ' — ' +
        run.issues_detected + ' found, ' + run.issues_repaired + ' fixed.');
      (d.issues || []).slice(0, 4).forEach(function (issue) {
        var li = document.createElement('li');
        li.className = 'callout ' + (issue.severity === 'critical' ? 'error' : (issue.severity === 'warning' ? 'warn' : 'info'));
        li.textContent = issue.description;
        list.appendChild(li);
      });
    }).catch(function () {});
  }

  refreshSquare();
  refreshRecon();
  setInterval(function () {
    if (document.visibilityState === 'visible') { refreshSquare(); refreshRecon(); }
  }, 30000);
})();
