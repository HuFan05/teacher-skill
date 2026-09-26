'use strict';
(() => {
  const input = document.getElementById('search');
  const results = document.getElementById('results');
  const status = document.getElementById('search-status');
  let searchLoaded = false, timer;
  let language = document.body.dataset.defaultLanguage || 'en';
  try { language = localStorage.getItem('crs-reading-language') || language; } catch (_) {}
  const selected = new URLSearchParams(location.search).get('lang');
  if (selected === 'en' || selected === 'zh') language = selected;
  if (!['en', 'zh'].includes(language)) language = 'en';
  const bodies = JSON.parse(document.getElementById('language-content').textContent);
  const article = document.getElementById('content');
  const bilingual = Boolean(bodies.zh);
  let generation = 0;
  function carryLanguage() {
    document.querySelectorAll('a[href]').forEach(a => {
      if (a.getAttribute('href').startsWith('#')) return;
      const url = new URL(a.getAttribute('href'), location.href);
      if (url.pathname.endsWith('.html')) { url.searchParams.set('lang', language); a.href = url.href; }
    });
  }
  function formulaLineWidth(el) {
    // Measure the containing text block, never the hint or the scroll wrapper.
    let block = el.parentElement;
    while (block && ['inline', 'contents'].includes(getComputedStyle(block).display)) block = block.parentElement;
    if (!block) return 0;
    const style = getComputedStyle(block);
    const scale = block.offsetWidth > 0 ? block.getBoundingClientRect().width / block.offsetWidth : 1;
    return (block.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0)) * scale;
  }
  function markFormulaOverflow() {
    document.querySelectorAll('#content .formula[data-tex], .hero .formula[data-tex]').forEach(el => {
      const svg = el.querySelector('svg');
      const available = formulaLineWidth(el);
      // SVG geometry is independent of generated labels and old overflow state.
      const naturalWidth = svg ? svg.getBoundingClientRect().width : 0;
      const overflow = available > 0 && naturalWidth > available + 1;
      el.dataset.overflow = String(overflow);
      if (overflow) el.dataset.scrollHint = language === 'zh' ? '左右滑动查看完整公式 →' : 'Scroll horizontally for the complete formula →';
      else delete el.dataset.scrollHint;
    });
  }
  let layoutFrame;
  function scheduleFormulaLayout() {
    cancelAnimationFrame(layoutFrame);
    layoutFrame = requestAnimationFrame(markFormulaOverflow);
  }
  window.addEventListener('resize', scheduleFormulaLayout);
  document.addEventListener('toggle', scheduleFormulaLayout, true);
  window.addEventListener('afterprint', scheduleFormulaLayout);
  document.fonts?.ready.then(scheduleFormulaLayout);
  async function renderFormulas() {
    const current = ++generation;
    document.body.dataset.formulaReady = 'false';
    const formulas = [...document.querySelectorAll('#content .formula[data-tex], .hero .formula[data-tex]')];
    let failures = 0;
    if (formulas.length) {
      if (!window.MathJax?.startup?.promise) failures = formulas.length;
      else {
        await MathJax.startup.promise;
        for (const el of formulas) {
          if (current !== generation) return;
          try {
            const node = await MathJax.tex2svgPromise(el.dataset.tex, {display: el.dataset.display === 'true'});
            if (node.querySelector('[data-mml-node="merror"]')) failures++;
            el.replaceChildren(node);
          } catch (error) { failures++; el.textContent = el.dataset.tex; el.title = String(error); }
        }
      }
    }
    if (current !== generation) return;
    markFormulaOverflow();
    document.body.dataset.formulaErrors = String(failures);
    document.body.dataset.formulaReady = 'true';
    if (failures) {
      const warning = document.createElement('p'); warning.className = 'notice';
      warning.textContent = '部分公式未能渲染，请检查本地公式渲染器。'; article.prepend(warning);
    }
    if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
  }
  async function setLanguage(next) {
    language = next;
    // Keep explicit URL language aligned with the reader's new choice.
    try {
      const url = new URL(location.href);
      url.searchParams.set('lang', next);
      history.replaceState(null, '', url.href);
    } catch (_) {}
    try { localStorage.setItem('crs-reading-language', next); } catch (_) {}
    article.innerHTML = bodies[bilingual ? next : 'en'];
    article.lang = bilingual && next === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-language]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.language === next)));
    carryLanguage();
    await renderFormulas();
  }
  function search() {
    const q = input.value.trim().toLowerCase(); results.replaceChildren();
    if (!q) { status.textContent = ''; return; }
    if (!window.CRS_SEARCH) { status.textContent = '正在加载本地搜索索引…'; return; }
    const words = q.split(/\s+/);
    const found = window.CRS_SEARCH.filter(x => words.every(w => (x.title + ' ' + x.text).toLowerCase().includes(w)));
    status.textContent = `找到 ${found.length} 页，显示前 50 页`;
    for (const item of found.slice(0, 50)) {
      const a = document.createElement('a'); a.href = item.url; a.textContent = item.title;
      const small = document.createElement('small'); small.textContent = item.group; a.append(small); results.append(a);
    }
    carryLanguage();
  }
  function loadSearch() {
    if (searchLoaded) return;
    searchLoaded = true;
    const script = document.createElement('script'); script.src = 'search-index.js';
    script.onerror = () => { searchLoaded = false; status.textContent = '搜索索引加载失败，请确认网页文件夹完整。'; };
    document.head.append(script);
  }
  const navToggle = document.querySelector('.nav-toggle');
  navToggle?.addEventListener('click', () => {
    const opened = navToggle.getAttribute('aria-expanded') !== 'true';
    navToggle.setAttribute('aria-expanded', String(opened));
    document.querySelector('aside').dataset.open = String(opened);
  });
  input.addEventListener('focus', loadSearch);
  input.addEventListener('input', () => { loadSearch(); clearTimeout(timer); timer = setTimeout(search, 180); });
  window.addEventListener('crs-search-ready', search);
  document.getElementById('print').addEventListener('click', () => window.print());
  document.querySelectorAll('[data-language]').forEach(b => b.addEventListener('click', () => setLanguage(b.dataset.language)));
  window.addEventListener('load', () => setLanguage(language));
})();
