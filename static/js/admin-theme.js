/* Dark mode switch on the admin pages. Same saved setting as the app's sidebar switch. */
(function () {
  function label() {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    document.querySelectorAll('[data-theme-label]').forEach(function (el) {
      el.textContent = dark ? 'Light mode' : 'Dark mode';
    });
  }
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-theme-toggle]')) return;
    var dark = document.documentElement.getAttribute('data-theme') !== 'dark';
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    try { localStorage.setItem('pinksheet-theme', dark ? 'dark' : 'light'); } catch (err) {}
    label();
  });
  label();
})();
