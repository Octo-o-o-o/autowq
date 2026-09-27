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

/* ── 配色切换：自动 → 亮 → 暗 循环；自动档跟随系统并实时响应变化 ── */
(function () {
  'use strict';
  var btn = document.getElementById('themeToggle');
  if (!btn) return;
  var root = document.documentElement;
  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  var ZH = (root.lang || '').indexOf('zh') === 0;
  var LABELS = {
    auto: ZH ? '配色：跟随系统（点击切换）' : 'Theme: follow system (click to switch)',
    light: ZH ? '配色：浅色（点击切换）' : 'Theme: light (click to switch)',
    dark: ZH ? '配色：深色（点击切换）' : 'Theme: dark (click to switch)'
  };
  var ICONS = { auto: '\u25D0', light: '\u2600', dark: '\u263E' }; /* ◐ ☀ ☾ */
  function mode() {
    var m = root.dataset.themeMode;
    return (m === 'light' || m === 'dark') ? m : 'auto';
  }
  function apply(m, persist) {
    var dark = m === 'dark' || (m === 'auto' && mq.matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    root.dataset.themeMode = m;
    if (persist) { try { localStorage.setItem('wq-theme', m); } catch (e) {} }
    btn.textContent = ICONS[m];
    btn.title = LABELS[m];
    btn.setAttribute('aria-label', LABELS[m]);
    var color = dark ? '#0b0e0c' : '#f5f3ec';
    Array.prototype.forEach.call(document.querySelectorAll('meta[name="theme-color"]'), function (mt) {
      mt.setAttribute('content', color);
    });
  }
  apply(mode(), false);
  btn.addEventListener('click', function () {
    var next = { auto: 'light', light: 'dark', dark: 'auto' }[mode()] || 'auto';
    apply(next, true);
  });
  function onSystemChange() { if (mode() === 'auto') apply('auto', false); }
  if (mq.addEventListener) mq.addEventListener('change', onSystemChange);
  else if (mq.addListener) mq.addListener(onSystemChange);
})();
