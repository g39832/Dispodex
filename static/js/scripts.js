/* eBay script builder: load a SKU, keep prompt / answer / final text saved per SKU. */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var boilerplate = JSON.parse(document.getElementById('final-boilerplate').textContent);
  var skuInput = document.getElementById('script-sku');
  var prompt = document.getElementById('prompt-text');
  var answer = document.getElementById('chatgpt-text');
  var final = document.getElementById('final-text');
  var facts = document.getElementById('script-facts');
  var state = document.getElementById('script-state');
  var loadedSku = '';
  var saveTimer = null;

  function setState(text, tone) { state.textContent = text; state.className = 'save-state ' + (tone || ''); }

  function buildFinal(text) {
    text = (text || '').trim();
    return text ? boilerplate + '\n\n' + text : '';
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
      final.value = d.final_text || buildFinal(d.chatgpt_text);
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
  document.getElementById('build-final').addEventListener('click', function () { final.value = buildFinal(answer.value); save(); });
  document.getElementById('clear-answer').addEventListener('click', function () { answer.value = ''; final.value = ''; save(); answer.focus(); });
  answer.addEventListener('input', function () { final.value = buildFinal(answer.value); scheduleSave(); });
  prompt.addEventListener('input', scheduleSave);
  final.addEventListener('input', scheduleSave);
  window.addEventListener('pagehide', function () { if (saveTimer) save(); });

  if (skuInput.value.trim()) load(skuInput.value);
})();
