/* Zebra label printing through QZ Tray (https://qz.io).
 *
 * The server builds the ZPL; QZ Tray (installed on the computer with the
 * printer) sends it raw to the first printer whose name contains "zebra" or
 * "zpl". If QZ Tray isn't running, print buttons show a clear message.
 *
 *   PinksheetLabels.print(sku, preset, button)
 */
(function () {
  'use strict';
  var P = window.Pinksheet;
  var ready = null;

  function connect() {
    if (!window.qz) return Promise.reject(new Error('QZ Tray script not loaded'));
    if (qz.websocket.isActive()) return Promise.resolve();
    if (ready) return ready;
    ready = fetch('/qz/certificate/', { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .catch(function () { return null; })
      .then(function (cert) {
        if (cert) {
          qz.security.setCertificatePromise(function (resolve) { resolve(cert); });
          qz.security.setSignatureAlgorithm('SHA512');
          qz.security.setSignaturePromise(function (toSign) {
            return function (resolve, reject) {
              fetch('/qz/sign/', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ request: toSign })
              }).then(function (r) { if (!r.ok) throw new Error('Signing failed'); return r.text(); }).then(resolve, reject);
            };
          });
        }
        return qz.websocket.connect({ retries: 2, delay: 1 });
      })
      .catch(function (err) { ready = null; throw err; });
    return ready;
  }

  function findPrinter() {
    return qz.printers.find().then(function (printers) {
      if (!printers || !printers.length) throw new Error('No printers found. Is the Zebra printer installed?');
      var zebra = printers.filter(function (name) { return /zebra|zpl/i.test(name || ''); })[0];
      return zebra || printers[0];
    });
  }

  function print(sku, preset, button) {
    sku = (sku || '').trim();
    if (!sku) { P.toast('Enter a SKU before printing a sticker.', 'err'); return; }
    var original = button ? button.innerHTML : '';
    if (button) { button.disabled = true; button.textContent = 'Preparing label…'; }
    var restore = function () { if (button) { button.disabled = false; button.innerHTML = original; } };

    P.api('/api/labels/zpl/?sku=' + encodeURIComponent(sku) + '&preset=' + encodeURIComponent(preset || 'compact'))
      .then(function (label) {
        if (button) button.textContent = 'Connecting to printer…';
        return connect().then(findPrinter).then(function (printer) {
          var cfg = qz.configs.create(printer);
          return qz.print(cfg, [{ type: 'raw', format: 'command', flavor: 'plain', data: label.zpl }]).then(function () {
            P.toast('Label for ' + label.sku + ' sent to ' + printer);
          });
        });
      })
      .catch(function (err) {
        var msg = err && err.message ? err.message : String(err);
        if (/websocket|connect|unable to establish/i.test(msg)) msg = 'QZ Tray isn’t running on this computer. Start QZ Tray and try again.';
        P.toast('Label not printed: ' + msg, 'err');
      })
      .finally(restore);
  }

  window.PinksheetLabels = { print: print };
})();
