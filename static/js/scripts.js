/* eBay script builder: load a SKU, keep prompt / answer / final text saved per SKU. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var skuInput = document.getElementById('script-sku');
  var prompt = document.getElementById('prompt-text');
  var answer = document.getElementById('chatgpt-text');
  var final = document.getElementById('final-text');
  var title = document.getElementById('final-title');
  var titleCount = document.getElementById('title-count');
  var notesBox = document.getElementById('staff-notes-box');
  var notes = document.getElementById('staff-notes');
  var facts = document.getElementById('script-facts');
  var state = document.getElementById('script-state');
  var loadedSku = '';
  var saveTimer = null;
  var buildTimer = null;
  var buildRun = 0;

  function setState(text, tone) { state.textContent = text; state.className = 'save-state ' + (tone || ''); }

  // Title, description and staff notes picked out of ChatGPT's answer (done on the server).
  function showListing(d) {
    title.value = d.title || '';
    var over = (d.title_length || 0) > (d.title_limit || 80);
    titleCount.textContent = d.title ? '· ' + d.title_length + ' / ' + d.title_limit + ' characters' + (over ? ', too long for eBay' : '') : '';
    titleCount.className = over ? 'err-text' : 'faint';
    notes.textContent = d.staff_notes || '';
    notesBox.hidden = !d.staff_notes;
  }
  function buildFinal(andSave) {
    var run = ++buildRun;
    return P.api('/api/script-build/', { method: 'POST', body: { chatgpt_text: answer.value } }).then(function (d) {
      if (run !== buildRun) return;  // a newer paste is already being built
      showListing(d);
      final.value = d.final_text;
      if (andSave) save();
    }).catch(function (err) { setState('Could not build: ' + err.message, 'err'); });
  }

  function renderFacts(list) {
    facts.innerHTML = '';
    if (!list.length) { facts.innerHTML = '<p class="hint">No item on file for this SKU.</p>'; return; }
    list.forEach(function (f) {
      var row = document.createElement('div');
      var label = document.createElement('span');
      var value = document.createElement('span');
      label.textContent = f.label;
      value.textContent = f.value;
      row.appendChild(label);
      row.appendChild(value);
      facts.appendChild(row);
    });
  }

  function save() {
    if (!loadedSku) return;
    clearTimeout(saveTimer);
    setState('Saving…', 'saving');
    P.api('/api/scripts/' + encodeURIComponent(loadedSku) + '/', {
      method: 'POST', keepalive: true,
      body: { sku_display: loadedSku, prompt_text: prompt.value, chatgpt_text: answer.value, final_text: final.value }
    }).then(function (d) { setState('Saved ' + d.saved_at, 'saved'); })
      .catch(function (err) { setState('Not saved: ' + err.message, 'err'); });
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(save, 700); }

  function load(sku) {
    sku = (sku || '').trim().toUpperCase();
    if (!sku) { setState('Enter a SKU first', 'warn'); return; }
    setState('Loading…', 'saving');
    P.api('/api/scripts/' + encodeURIComponent(sku) + '/').then(function (d) {
      loadedSku = d.sku;
      skuInput.value = d.sku;
      renderFacts(d.facts || []);
      prompt.value = d.prompt_text || '';
      answer.value = d.chatgpt_text || '';
      final.value = d.final_text || '';
      showListing(d);
      document.getElementById('script-open-intake').href = '/intake/?sku=' + encodeURIComponent(d.sku);
      var imagesLink = document.getElementById('script-open-images');  // absent while Listing images is turned off
      if (imagesLink) imagesLink.href = '/listing-images/?sku=' + encodeURIComponent(d.sku);
      history.replaceState(null, '', '?sku=' + encodeURIComponent(d.sku));
      setState(d.found ? (d.saved_at ? 'Loaded saved script' : 'Prompt ready') : 'No item on file for ' + d.sku, d.found ? 'saved' : 'warn');
    }).catch(function (err) { setState(err.message, 'err'); });
  }

  function copy(text, what) {
    if (!text.trim()) { P.toast('Nothing to copy yet.', 'err'); return; }
    var done = function () { P.toast(what + ' copied'); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text);
      done();
    }
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
  }

  document.getElementById('script-form').addEventListener('submit', function (e) { e.preventDefault(); load(skuInput.value); });
  document.getElementById('copy-prompt').addEventListener('click', function () { copy(prompt.value, 'Prompt'); });
  document.getElementById('copy-final').addEventListener('click', function () { copy(final.value, 'Final description'); });
  document.getElementById('copy-title').addEventListener('click', function () { copy(title.value, 'Title'); });
  document.getElementById('rebuild-prompt').addEventListener('click', function () {
    if (!loadedSku) return;
    P.api('/api/scripts/' + encodeURIComponent(loadedSku) + '/?fresh=1').then(function (d) {
      renderFacts(d.facts || []);
      if (!d.found) { P.toast('No item on file for ' + loadedSku, 'err'); return; }
      prompt.value = d.prompt_text;
      save();
      P.toast('Prompt rebuilt from the latest item details');
    }).catch(function (err) { P.toast(err.message, 'err'); });
  });
  document.getElementById('build-final').addEventListener('click', function () { buildFinal(true); });
  document.getElementById('clear-answer').addEventListener('click', function () {
    answer.value = ''; final.value = ''; showListing({}); save(); answer.focus();
  });
  answer.addEventListener('input', function () {
    clearTimeout(buildTimer);
    buildTimer = setTimeout(function () { buildFinal(true); }, 350);
  });
  prompt.addEventListener('input', scheduleSave);
  final.addEventListener('input', scheduleSave);
  window.addEventListener('pagehide', function () { if (saveTimer) save(); });

  if (skuInput.value.trim()) load(skuInput.value);
})();
