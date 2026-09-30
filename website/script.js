// autowq 官网动效：onboard 打字机、数字滚动、进入视口上浮
document.documentElement.classList.add('js');
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

/* ── 下载：按访客平台换 Hero 主按钮、高亮对应卡片；命令一键复制 ── */
(function () {
  'use strict';
  var ZH = (document.documentElement.lang || '').indexOf('zh') === 0;
  var ua = navigator.userAgent || '';
  var isWin = /Windows/i.test(ua);
  var isMac = /Mac/i.test(ua) && !isWin;

  var hero = document.getElementById('heroCta');
  if (hero) {
    if (isMac) {
      hero.href = 'https://github.com/Octo-o-o-o/autowq/releases/download/v0.2.18/WorldQuant-0.2.18.dmg';
      hero.textContent = ZH ? '下载 macOS 版 · 54 MB' : 'Download for macOS · 54 MB';
    } else if (isWin) {
      hero.href = 'https://github.com/Octo-o-o-o/autowq/releases/download/v0.2.18/WorldQuantTray.exe';
      hero.textContent = ZH ? '下载 Windows 版 · 17 MB' : 'Download for Windows · 17 MB';
    }
  }

  /* Hero 下载下拉：主按钮直下，箭头切换系统 / 安装方式 */
  var more = document.getElementById('heroMore');
  var menu = document.getElementById('heroMenu');
  if (more && menu) {
    var dl = more.parentNode;
    var mine = menu.querySelector(isWin ? '[data-os="windows"]' : (isMac ? '[data-os="macos"]' : 'x'));
    if (mine) {
      var chip = document.createElement('span');
      chip.className = 'cm-chip';
      chip.textContent = ZH ? '你的系统' : 'Your system';
      mine.querySelector('.cm-l').appendChild(chip);
    }
    var items = function () {
      return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
    };
    function setOpen(open) {
      menu.hidden = !open;
      more.setAttribute('aria-expanded', open ? 'true' : 'false');
      dl.classList.toggle('open', open);
    }
    more.addEventListener('click', function (e) {
      e.stopPropagation();
      setOpen(menu.hidden);
    });
    more.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); items()[0].focus(); }
    });
    menu.addEventListener('keydown', function (e) {
      var list = items();
      var i = list.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      else if (e.key === 'Escape') { setOpen(false); more.focus(); }
      else if (e.key === 'Tab') { setOpen(false); }
    });
    menu.addEventListener('focusin', function (e) {
      items().forEach(function (it) { it.classList.toggle('focus', it === e.target); });
    });
    document.addEventListener('click', function (e) { if (!dl.contains(e.target)) setOpen(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });

    menu.querySelectorAll('a[role="menuitem"]').forEach(function (a) {
      a.addEventListener('click', function () { setOpen(false); });
    });
    menu.querySelectorAll('[data-cmd]').forEach(function (b) {
      b.addEventListener('click', function () {
        var text = b.dataset.cmd;
        var r = b.querySelector('.cm-r');
        var orig = r.textContent;
        function done() {
          r.textContent = ZH ? '已复制 ✓' : 'Copied ✓';
          setTimeout(function () {
            setOpen(false);
            setTimeout(function () { r.textContent = orig; }, 300);
          }, 900);
        }
        function fallback() {
          var t = document.createElement('textarea');
          t.value = text;
          document.body.appendChild(t);
          t.select();
          try { document.execCommand('copy'); done(); } catch (e) {}
          t.remove();
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, fallback);
        } else { fallback(); }
      });
    });
  }

  var sel = isWin ? '.dl-card[data-os="windows"]' : (isMac ? '.dl-card.featured' : null);
  var card = sel && document.querySelector(sel);
  if (card && !card.classList.contains('featured')) {
    card.classList.add('yours');
    var tag = card.querySelector('.dl-tag');
    if (tag) {
      var chip = document.createElement('span');
      chip.className = 'yours-chip';
      chip.textContent = ZH ? '你的系统' : 'Your system';
      tag.appendChild(chip);
    }
  }

  document.querySelectorAll('.copy').forEach(function (b) {
    b.addEventListener('click', function () {
      var text = b.dataset.copy;
      function done() {
        b.classList.add('done');
        b.textContent = ZH ? '已复制' : 'Copied';
        setTimeout(function () {
          b.classList.remove('done');
          b.textContent = ZH ? '复制' : 'Copy';
        }, 1600);
      }
      function fallback() {
        var t = document.createElement('textarea');
        t.value = text;
        document.body.appendChild(t);
        t.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        t.remove();
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else { fallback(); }
    });
  });
})();
