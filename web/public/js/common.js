/* ============================================================
   公共布局逻辑 — 主题切换 / 浮动导航 / 搜索历史工具
   顶栏(Topbar)与页脚(Footer)已由 React 组件渲染(web/src/components)，
   搜索弹层由 React 的 SearchOverlay 组件接管；本文件保留纯逻辑工具函数。
   ============================================================ */

// 读取登录用户(本地缓存)
function getStoredAdmin() {
  try { return JSON.parse(localStorage.getItem('admin') || 'null'); }
  catch (e) { return null; }
}

// 退出登录：先调接口吊销 refresh token（登出后旧 refresh 无法再换新），再清本地凭证
async function doLogout() {
  try {
    const rt = localStorage.getItem('refreshToken');
    if (rt) await api.post('/api/auth/logout', { refreshToken: rt });
  } catch (e) { /* 网络失败也继续本地登出 */ }
  clearAuth();
  toast('已退出登录');
  setTimeout(() => location.reload(), 500);
}

// 渲染浮动导航组：返回按钮（文章页）+ 返回顶部（全局）
function renderBackToTop() {
  if (document.getElementById('float-nav')) return;
  const nav = document.createElement('div');
  nav.id = 'float-nav';
  nav.className = 'float-nav';
  // 返回按钮：只有文章详情页显示
  const isArticle = location.pathname.includes('/article.html') || !!document.getElementById('article-content');
  nav.innerHTML = ''
    + (isArticle ? '<button class="fn-back" title="返回文章列表" aria-label="返回"><span>←</span></button>' : '')
    + '<button class="fn-top" title="返回顶部" aria-label="返回顶部">↑</button>';
  document.body.appendChild(nav);

  const backBtn = nav.querySelector('.fn-back');
  const topBtn = nav.querySelector('.fn-top');

  const onScroll = () => {
    const show = window.scrollY > 400;
    nav.classList.toggle('show', show);
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  if (topBtn) {
    topBtn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      if (document.referrer && document.referrer.includes(location.host)) {
        history.back();
      } else {
        location.href = 'articles.html';
      }
    });
  }
  onScroll();
}

// 数字滚动动画：从当前值平滑增长到目标值
// el 目标元素，target 目标值，duration 动画时长(ms)，formatter 可选格式化函数
function animateNumber(el, target, duration = 1200, formatter) {
  if (!el) return;
  const start = 0;
  const startTime = performance.now();
  const fmt = formatter || ((n) => String(Math.round(n)));
  const step = (now) => {
    const t = Math.min(1, (now - startTime) / duration);
    // easeOutCubic：先快后慢，数字"刹住"的观感更自然
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = fmt(start + (target - start) * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = fmt(target);
  };
  requestAnimationFrame(step);
}

// 图片懒加载：给所有 data-src 的 img 元素延迟加载
// 用 IntersectionObserver（比 scroll 监听性能好），不支持时回退立即加载
function initLazyImages() {
  const imgs = document.querySelectorAll('img[data-src]');
  if (!imgs.length) return;
  if (!('IntersectionObserver' in window)) {
    imgs.forEach(img => { if (img.dataset.src) img.src = img.dataset.src; });
    return;
  }
  const io = new IntersectionObserver((entries, obs) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        const img = e.target;
        if (img.dataset.src) {
          img.src = img.dataset.src;
          img.removeAttribute('data-src');
        }
        obs.unobserve(img);
      }
    });
  }, { rootMargin: '50px' });
  imgs.forEach(img => io.observe(img));
}

// 渲染每日一言(页面有 .quote 时)
async function renderQuote() {
  const quoteEl = document.querySelector('.quote blockquote');
  if (!quoteEl) return;
  try {
    const data = await api.get('/api/quote/random');
    quoteEl.innerHTML = `<p>${esc(data.content)}</p><cite>—— ${esc(data.author)}</cite>`;
  } catch (e) {
    quoteEl.innerHTML = `<p>把每一个今天都当作新的开始。</p><cite>—— 佚名</cite>`;
  }
}

// 站点信息填充页脚社交链接
async function renderSiteInfo() {
  try {
    const site = await api.get('/api/site');
    if (site.github) { const el = document.getElementById('ft-github'); if (el) el.href = site.github; }
    if (site.email) { const el = document.getElementById('ft-email'); if (el) el.href = 'mailto:' + site.email; }
  } catch (e) { /* 静默,用默认值 */ }
}

// 关键词高亮：在已转义的文本里给匹配段加 <mark>
function highlightKeyword(escapedText, keyword) {
  if (!keyword) return escapedText;
  // 转义关键词里的正则特殊字符，避免注入
  const safe = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return escapedText.replace(new RegExp(safe, 'gi'), (m) => `<mark>${m}</mark>`);
}

// ============ 搜索历史（localStorage） ============
function getSearchHistory() {
  try { return JSON.parse(localStorage.getItem('search_history') || '[]'); }
  catch (e) { return []; }
}

function saveSearchHistory(q) {
  if (!q) return;
  let list = getSearchHistory();
  // 去重（大小写不敏感），放到最前
  list = list.filter(item => item.toLowerCase() !== q.toLowerCase());
  list.unshift(q);
  // 只保留最近 8 条
  if (list.length > 8) list = list.slice(0, 8);
  localStorage.setItem('search_history', JSON.stringify(list));
}

function clearSearchHistory() {
  localStorage.removeItem('search_history');
}

// 初始化页面公共部分
async function initPage(activeName) {
  // 写入导航高亮标记，供 React Topbar 组件读取
  if (activeName) document.body.dataset.activeNav = activeName;
  initTheme();
  renderQuote();
  renderSiteInfo();
  renderBackToTop();
  initScrollProgress();
  initRipple();
  // 后台页面是独立布局，不在 topbar 体系内，跳过懒加载
  if (!location.pathname.includes('/admin/')) {
    initLazyImages();
    loadBgFlow(); // 前台页面才启用流动线条背景
  }
}

// ============ 界面交互效果（按 ui-interaction-effects.md 精选） ============

// 滚动进度条：文章页复用已有 #reading-progress，其他页面动态创建顶部红线
function initScrollProgress() {
  if (window.__progressInited) return;
  window.__progressInited = true;
  let bar = document.getElementById('reading-progress');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'scroll-progress';
    bar.id = 'reading-progress';
    document.body.appendChild(bar);
  }
  const update = () => {
    const h = document.documentElement;
    const total = h.scrollHeight - h.clientHeight;
    bar.style.width = (total > 0 ? (h.scrollTop / total) * 100 : 0) + '%';
  };
  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  update();
}

// 点击涟漪：事件委托，按钮首次点击时附加 .ripple-btn（overflow hidden）
function initRipple() {
  if (window.__rippleInited) return;
  window.__rippleInited = true;
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn, .btn-shine, .auth-btn, button');
    if (!btn) return;
    if (!btn.classList.contains('ripple-btn')) btn.classList.add('ripple-btn');
    const rect = btn.getBoundingClientRect();
    const r = document.createElement('span');
    r.className = 'ripple';
    r.style.left = (e.clientX - rect.left) + 'px';
    r.style.top = (e.clientY - rect.top) + 'px';
    btn.appendChild(r);
    setTimeout(() => r.remove(), 750);
  });
}

// 列表条目骨架：生成 n 行 .skel-entry（对应目录表的序号/标题/分类/日期）
function skelEntries(n) {
  let html = '';
  for (let i = 0; i < n; i++) {
    html += '<div class="skel-entry">' +
      '<div class="skeleton s-num"></div>' +
      '<div class="skeleton s-title"></div>' +
      '<div class="skeleton s-cat"></div>' +
      '<div class="skeleton s-date"></div>' +
      '</div>';
  }
  return html;
}
// ============================================================
// 图片灯箱：点击文章内图片放大查看
// 在 article.js 的 renderArticle 后调用 initLightbox()
// ============================================================
function initLightbox() {
  const content = document.getElementById('article-content');
  if (!content) return;
  const imgs = content.querySelectorAll('img');
  if (!imgs.length) return;

  // 构建灯箱 DOM（全局只一个）
  let lb = document.getElementById('lightbox');
  if (!lb) {
    lb = document.createElement('div');
    lb.id = 'lightbox';
    lb.className = 'lightbox';
    lb.innerHTML = `
      <button class="lightbox-close" aria-label="关闭">×</button>
      <button class="lightbox-prev" aria-label="上一张">‹</button>
      <img class="lightbox-img" alt="预览大图">
      <button class="lightbox-next" aria-label="下一张">›</button>
      <div class="lightbox-caption"></div>`;
    document.body.appendChild(lb);

    lb.addEventListener('click', (e) => {
      if (e.target === lb || e.target.classList.contains('lightbox-close')) closeLightbox();
    });
    lb.querySelector('.lightbox-prev').addEventListener('click', (e) => { e.stopPropagation(); lightboxNav(-1); });
    lb.querySelector('.lightbox-next').addEventListener('click', (e) => { e.stopPropagation(); lightboxNav(1); });
    document.addEventListener('keydown', (e) => {
      if (!lb.classList.contains('show')) return;
      if (e.key === 'Escape') closeLightbox();
      else if (e.key === 'ArrowLeft') lightboxNav(-1);
      else if (e.key === 'ArrowRight') lightboxNav(1);
    });
  }

  // 收集所有图片到数组，方便上一张/下一张切换
  const list = Array.from(imgs).map(img => ({
    src: img.src,
    alt: img.alt || img.title || '',
  }));

  imgs.forEach((img, i) => {
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', () => openLightbox(i, list));
  });
}

let lightboxIndex = 0;
let lightboxList = [];

function openLightbox(index, list) {
  lightboxIndex = index;
  lightboxList = list;
  const lb = document.getElementById('lightbox');
  updateLightbox();
  lb.classList.add('show');
  document.body.style.overflow = 'hidden';
}

function updateLightbox() {
  const lb = document.getElementById('lightbox');
  const img = lb.querySelector('.lightbox-img');
  const caption = lb.querySelector('.lightbox-caption');
  const item = lightboxList[lightboxIndex];
  if (!item) return;
  img.src = item.src;
  img.alt = item.alt;
  caption.textContent = item.alt ? `${item.alt}（${lightboxIndex + 1}/${lightboxList.length}）` : `${lightboxIndex + 1}/${lightboxList.length}`;
  // 只有一张时隐藏左右按钮
  lb.querySelector('.lightbox-prev').style.display = lightboxList.length > 1 ? '' : 'none';
  lb.querySelector('.lightbox-next').style.display = lightboxList.length > 1 ? '' : 'none';
}

function lightboxNav(dir) {
  lightboxIndex = (lightboxIndex + dir + lightboxList.length) % lightboxList.length;
  updateLightbox();
}

function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.classList.remove('show');
  document.body.style.overflow = '';
}

// 动态加载背景流动线条脚本（前台所有页面共享，避免逐页改 <script>）
function loadBgFlow() {
  if (document.getElementById('bg-flow-script')) return;
  const s = document.createElement('script');
  s.id = 'bg-flow-script';
  s.src = 'js/bg-flow.js?v=5';
  document.body.appendChild(s);
}
