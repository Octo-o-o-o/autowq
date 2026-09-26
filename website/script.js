// autowq 官网动效：onboard 打字机、数字滚动、进入视口上浮
(function () {
  'use strict';

  /* ── onboard 打字机 ── */
  var term = document.getElementById('typewriter');
  if (term) {
    var LINES = window.ONBOARD_LINES || [];
    var li = 0, ci = 0, out = '';
    function render(cursor) {
      term.innerHTML = out + (cursor ? '<span class="cursor"></span>' : '');
    }
    function tick() {
      if (li >= LINES.length) {
        render(false);
        setTimeout(function () { li = 0; ci = 0; out = ''; render(true); setTimeout(tick, 600); }, 4200);
        return;
      }
      var line = LINES[li];
      if (ci <= line.text.length) {
        var shown = line.text.slice(0, ci);
        var html = '<span class="' + line.cls + '">' + shown.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</span>';
        term.innerHTML = out + html + '<span class="cursor"></span>';
        ci++;
        setTimeout(tick, line.fast ? 14 : 34);
      } else {
        out += '<span class="' + line.cls + '">' + line.text.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</span>\n';
        li++; ci = 0;
        render(true);
        setTimeout(tick, line.pause || 260);
      }
    }
    tick();
  }

  /* ── 数字滚动 ── */
  function countUp(el) {
    var target = parseFloat(el.dataset.count);
    var decimals = (el.dataset.count.split('.')[1] || '').length;
    var t0 = null;
    function step(ts) {
      if (!t0) t0 = ts;
      var p = Math.min((ts - t0) / 1400, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add('on');
      e.target.querySelectorAll('[data-count]').forEach(function (el) {
        if (!el.dataset.done) { el.dataset.done = '1'; countUp(el); }
      });
      if (e.target.hasAttribute('data-count') && !e.target.dataset.done) {
        e.target.dataset.done = '1'; countUp(e.target);
      }
      io.unobserve(e.target);
    });
  }, { threshold: 0.25 });

  document.querySelectorAll('.rise, [data-count]').forEach(function (el) { io.observe(el); });
})();
