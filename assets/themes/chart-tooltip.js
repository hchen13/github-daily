// Chart crosshair tooltip: hover any day-column to show all series
// for that day, sorted descending by value. Also moves a vertical
// guide line. Loaded into both the standalone publication.html and
// the web UI's chrome.
(function () {
  function initChart(wrapper) {
    var svg = wrapper.querySelector('svg.activity-chart');
    var tip = wrapper.querySelector('.chart-tooltip');
    if (!svg || !tip) return;
    var data;
    try { data = JSON.parse(svg.getAttribute('data-series')); }
    catch (e) { return; }
    var guide = svg.querySelector('.day-guide');
    var hits = svg.querySelectorAll('.day-hit');

    hits.forEach(function (rect) {
      rect.addEventListener('mouseenter', function () {
        var idx = parseInt(rect.getAttribute('data-day-idx'), 10);
        var day = data[idx];
        if (!day) return;
        var sorted = day.items.slice().sort(function (a, b) { return b.value - a.value; });
        var html = '<div class="tip-date">' + day.date + '</div>';
        sorted.forEach(function (s) {
          html += '<div class="tip-row">'
               + '<span class="tip-swatch" style="background:' + s.color + '"></span>'
               + '<span class="tip-name">' + s.name + '</span>'
               + '<span class="tip-value">' + s.value + '</span>'
               + '</div>';
        });
        tip.innerHTML = html;
        tip.hidden = false;
        if (guide) {
          var cx = rect.getAttribute('data-cx');
          guide.setAttribute('x1', cx);
          guide.setAttribute('x2', cx);
          guide.setAttribute('visibility', 'visible');
        }
      });
      rect.addEventListener('mousemove', function (e) {
        var wr = wrapper.getBoundingClientRect();
        var x = e.clientX - wr.left + 14;
        var y = e.clientY - wr.top - 12;
        var tw = tip.offsetWidth || 160;
        var th = tip.offsetHeight || 80;
        if (x + tw > wr.width - 4) x = e.clientX - wr.left - tw - 10;
        if (y + th > wr.height - 4) y = wr.height - th - 4;
        if (y < 0) y = 4;
        tip.style.left = x + 'px';
        tip.style.top = y + 'px';
      });
      rect.addEventListener('mouseleave', function () {
        tip.hidden = true;
        if (guide) guide.setAttribute('visibility', 'hidden');
      });
    });
  }
  document.querySelectorAll('.chart-wrapper').forEach(initChart);
})();
