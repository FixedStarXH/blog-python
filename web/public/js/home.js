/* ============================================================
   首页逻辑 — 头条精选 + 分类索引 + 文章目录 + 站点统计
   ============================================================ */

initPage('home').then(loadHome);

// 首页精选分页状态：每页 6 篇，翻页浏览全部精选文章
// featuredSeed：每次进入页面生成一次随机种子，后端 RAND(seed) 排序——
// 同一次访问内翻页顺序稳定不重叠，每次刷新（新种子）文章顺序/组合都不同
let curPage = 1;
const pageSize = 6;
const featuredSeed = Math.floor(Math.random() * 2147483647);

async function loadHome() {
  // 骨架占位：分类索引 + 最新文章先显示加载骨架（替换转圈）
  const catList = document.getElementById('cat-list');
  if (catList && !catList.childElementCount) {
    catList.innerHTML = Array.from({ length: 4 }).map(() =>
      '<div class="skel-cat"><div class="skeleton s-name"></div><div class="skeleton s-cnt"></div></div>'
    ).join('');
  }
  const dirList = document.getElementById('dir-list');
  if (dirList && !dirList.childElementCount) dirList.innerHTML = skelEntries(6);
  bindFeatureSpot();
  try {
    // 站点统计
    const site = await api.get('/api/site');
    if (site.stats) {
      // 数字滚动动画：比直接显示更生动（animateNumber 在 common.js）
      const ac = site.stats.articleCount ?? 0;
      const vc = site.stats.viewCount ?? 0;
      // 文章数小，整数显示；浏览量可能很大，用 fmtNum 格式化（带 k/w）
      animateNumber(document.getElementById('stat-articles'), ac, 900);
      animateNumber(document.getElementById('stat-views'), vc, 1400, fmtNum);
    }
    if (site.title) document.getElementById('hero-brand').textContent = '● ' + site.title + ' · EST. ' + (site.stats?.foundYear ?? 2019);

    // 分类索引
    const cats = await api.get('/api/categories');
    renderCats(cats);

    await loadFeature();
  } catch (e) {
    toast(e.message, true);
    document.getElementById('dir-list').innerHTML = '<div class="empty">文章加载失败: ' + esc(e.message) + '</div>';
  }
}

// 加载精选文章 + 渲染目录 + 渲染分页
// 首页精选(每页 6 篇)：后台勾选"首页精选"的文章随机展示（ORDER BY RAND）
// 若后台未勾选任何精选，回退到最新文章，保证首页永远有内容
async function loadFeature() {
  const dirList = document.getElementById('dir-list');
  if (!dirList.childElementCount || !dirList.querySelector('.entry')) {
    dirList.innerHTML = skelEntries(6);
  }
  let data = await api.get(`/api/articles?page=${curPage}&pageSize=${pageSize}&sort=latest&featured=true&seed=${featuredSeed}`);
  if (!data.list || !data.list.length) {
    data = await api.get(`/api/articles?page=${curPage}&pageSize=${pageSize}&sort=latest`);
  }
  renderFeature(data.list);
  renderDir(data.list, data.total);
  renderPagination(data.total);
}

// 分页控件：原地翻页不刷新，页码样式复用全局 .pagination
function renderPagination(total) {
  const wrap = document.getElementById('pagination');
  if (!wrap) return;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  let html = '';
  html += `<button class="page-btn ${curPage <= 1 ? 'disabled' : ''}" data-page="${curPage - 1}">←</button>`;
  for (let i = 1; i <= totalPages; i++) {
    html += i === curPage
      ? `<span class="cur">${i}</span>`
      : `<button class="page-btn" data-page="${i}">${i}</button>`;
  }
  html += `<button class="page-btn ${curPage >= totalPages ? 'disabled' : ''}" data-page="${curPage + 1}">→</button>`;
  wrap.innerHTML = html;

  wrap.querySelectorAll('.page-btn:not(.disabled)').forEach(btn => {
    btn.addEventListener('click', () => {
      curPage = parseInt(btn.dataset.page, 10);
      loadFeature();
      // 平滑滚动回目录区顶部，翻页后不丢失位置感
      const dir = document.querySelector('.directory');
      if (dir) window.scrollTo({ top: dir.offsetTop - 72, behavior: 'smooth' });
    });
  });
}

function renderCats(cats) {
  const wrap = document.getElementById('cat-list');
  document.getElementById('cats-count').textContent = '01—' + String(cats.length).padStart(2, '0');
  wrap.innerHTML = cats.map((c, i) => `
    <a class="cat" href="articles.html?categoryId=${c.id}">
      <span class="idx">${String(i + 1).padStart(2, '0')}</span>
      <span class="name">${esc(c.name)}</span>
      <span class="cnt">${c.articleCount ?? 0} posts</span>
      <span class="bar"></span>
    </a>
  `).join('');
  if (!cats.length) wrap.innerHTML = '<div class="empty" style="grid-column:1/13">暂无分类</div>';
}

function renderFeature(list) {
  const sec = document.getElementById('feature-sec');
  if (!list || !list.length) {
    sec.innerHTML = '<div class="empty" style="grid-column:1/13">暂无文章</div>';
    return;
  }
  const a = list[0];
  const rawTitle = a.title || '';
  const title = esc(rawTitle);

  sec.innerHTML = `
    <div class="feature-label">
      <span class="flag">Featured · 头条精选</span>
      <span class="vol">VOL. 01</span>
    </div>
    <h2 class="feature-title">
      <span class="ln">${title}<span class="red-line"></span></span>
    </h2>
    <div class="feature-foot">
      <div class="feature-meta">
        <span>${esc(a.author?.nickname || '作者')}</span><span class="sep">·</span>
        <span>${fmtDate(a.createdAt)}</span><span class="sep">·</span>
        <span>${esc(a.category?.name || '未分类')}</span><span class="sep">·</span>
        <span>阅读 ${fmtMinutes(a.summary)} 分钟</span>
      </div>
      <a class="feature-link" href="article.html?id=${a.id}">阅读全文 <span class="arrow">→</span></a>
    </div>
  `;

  // 标题能放下就不换行；只有溢出容器才拆两行（最后一段压红下划线）
  const h2 = sec.querySelector('.feature-title');
  if (h2 && h2.scrollWidth > h2.clientWidth + 1 && title.length > 1) {
    const len = Math.ceil(title.length / 2);
    h2.innerHTML = `
      <span class="ln">${title.slice(0, len)}</span>
      <span class="ln">${title.slice(len)}<span class="red-line"></span></span>`;
  }
}

function renderDir(list, total) {
  document.getElementById('dir-total').textContent = total ?? list.length;
  const wrap = document.getElementById('dir-list');
  if (!list || !list.length) {
    wrap.innerHTML = '<div class="empty" style="grid-column:1/13">暂无文章</div>';
    return;
  }
  wrap.innerHTML = list.map((a, i) => `
    <a class="entry" href="article.html?id=${a.id}">
      <span class="num">${String(i + 1).padStart(3, '0')}</span>
      <span class="title"><span>${esc(a.title)}</span><span class="arrow">→</span></span>
      <span class="cat">${esc(a.category?.name || '未分类')}</span>
      <span class="date">${fmtDate(a.createdAt)}<span class="views">${fmtNum(a.viewCount)} views</span></span>
    </a>
  `).join('');
}

// 头条精选区光标聚光：鼠标坐标写入 CSS 变量，光斑跟随（.feature::before）
function bindFeatureSpot() {
  const spot = document.querySelector('.feature');
  if (!spot || spot.dataset.spotBound) return;
  spot.dataset.spotBound = '1';
  spot.addEventListener('pointermove', (e) => {
    const rect = spot.getBoundingClientRect();
    spot.style.setProperty('--x', (e.clientX - rect.left) + 'px');
    spot.style.setProperty('--y', (e.clientY - rect.top) + 'px');
  });
}

// 滚动字幕：技术栈关键词跑马灯，· 分隔，两份内容无缝循环
setTimeout(() => {
  const track = document.getElementById('marquee-track');
  if (!track) return;
  // 技术栈关键词——比分类名更有"滚动字幕"的科技感
  const stack = [
    'GO BACKEND', 'GIN', 'GORM', 'MYSQL', 'REDIS',
    'DOCKER', 'JWT', 'TCP/IP', 'GOROUTINE', 'CHANNEL',
    'CONTEXT', 'VUE 3', 'SWISS DESIGN'
  ];
  // 每个关键词用 · 分隔，拼成一整段文本
  const oneLap = stack.map(s => `<span>${esc(s)}</span>`).join('<i>·</i>');
  // 复制两份实现无缝循环（translateX -50% 移过一份的宽度）
  track.innerHTML = oneLap + oneLap;
}, 0);
