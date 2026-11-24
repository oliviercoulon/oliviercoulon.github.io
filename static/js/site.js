// Menu on small screens, e-mail assembly, publication search and filters. No external resources.
(function () {
  // mobile menu
  var burger = document.querySelector('.burger');
  if (burger) burger.addEventListener('click', function () {
    var open = document.body.classList.toggle('nav-open');
    burger.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // e-mail links: address assembled here, never written in full in the HTML
  document.querySelectorAll('a.mail').forEach(function (a) {
    var addr = a.dataset.u + '@' + a.dataset.d;
    a.href = 'mailto:' + addr;
    if (a.classList.contains('full')) a.textContent = addr;
  });

  // publications: text search + "selected" filter, empty years hidden
  var input = document.getElementById('pub-search');
  if (!input) return;
  var filter = 'all';
  var items = Array.prototype.slice.call(document.querySelectorAll('.year-group li'));
  var groups = document.querySelectorAll('.year-group');
  var count = document.getElementById('pub-count');
  var fold = function (s) { return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase(); };

  function apply() {
    var words = fold(input.value.trim()).split(/\s+/).filter(Boolean);
    var shown = 0;
    items.forEach(function (li) {
      var ok = (filter === 'all' || li.dataset.selected) &&
        words.every(function (w) { return li.dataset.text.indexOf(w) >= 0; });
      li.hidden = !ok;
      if (ok) shown++;
    });
    groups.forEach(function (g) {
      var visible = g.querySelectorAll('li:not([hidden])').length;
      g.hidden = visible === 0;
      g.querySelector('.year small').textContent = visible;
      var jump = document.querySelector('.years a[href="#' + g.id + '"]');
      if (jump) jump.classList.toggle('off', visible === 0);
    });
    count.textContent = (words.length || filter !== 'all') ? shown + ' of ' + items.length + ' publications shown' : '';
  }
  input.addEventListener('input', apply);
  document.querySelectorAll('.seg button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.seg button').forEach(function (x) { x.classList.toggle('on', x === b); });
      filter = b.dataset.filter;
      apply();
    });
  });
})();
