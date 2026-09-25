/* Printable card: draw the QR code, then print once the photos have loaded. */
(function () {
  'use strict';
  var url = JSON.parse(document.getElementById('card-qr-url').textContent);
  if (window.QRCode) {
    new QRCode(document.getElementById('qr'), { text: url, width: 124, height: 124, correctLevel: QRCode.CorrectLevel.H });
  }
  if (document.body.getAttribute('data-autoprint') !== '1') return;
  var printed = false;
  function go() { if (printed) return; printed = true; setTimeout(function () { window.focus(); window.print(); }, 150); }
  var images = Array.prototype.slice.call(document.images).filter(function (img) { return !img.complete; });
  if (!images.length) return go();
  var left = images.length;
  images.forEach(function (img) {
    var done = function () { left -= 1; if (left <= 0) go(); };
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
  });
  setTimeout(go, 4000);
})();
