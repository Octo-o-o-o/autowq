// 主题切换 + 代码复制
(function () {
  var saved = null;
  try { saved = localStorage.getItem('wq-theme'); } catch (e) {}
  if (saved === 'light') document.documentElement.setAttribute('data-theme', 'light');

  window.toggleTheme = function () {
    var light = document.documentElement.getAttribute('data-theme') === 'light';
    if (light) { document.documentElement.removeAttribute('data-theme'); }
    else { document.documentElement.setAttribute('data-theme', 'light'); }
    try { localStorage.setItem('wq-theme', light ? 'dark' : 'light'); } catch (e) {}
  };

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('.copy-btn');
    if (!btn) return;
    var pre = btn.parentElement.querySelector('pre') || btn.parentElement;
    var text = pre.innerText.replace(/^复制$|^Copy$/m, '').trim();
    navigator.clipboard.writeText(text).then(function () {
      var old = btn.textContent;
      btn.textContent = btn.dataset.done || 'OK';
      setTimeout(function () { btn.textContent = old; }, 1200);
    });
  });
})();
