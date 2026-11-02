/* ============================================================
   文章列表页 — 分类/标签/搜索/排序/分页（原地筛选）
   ============================================================ */

initPage('archive').then(initList);

let curPage = 1;
const pageSize = 10;

function initList() {
  const categoryId = getParam('categoryId');
  const tag = getParam('tag');
  const keyword = getParam('keyword');
  const sort = getParam('sort') || 'latest';
  curPage = parseInt(getParam('page') || '1', 10);

  const title = document.getElementById('page-title');
  const sub = document.getElementById('page-sub');
  if (categoryId) { title.textContent = '分类文章'; }
  else if (tag) { title.textContent = '标签: ' + tag; sub.textContent = 'TAG: ' + tag.toUpperCase(); }
  else if (keyword) { title.textContent = '搜索: ' + keyword; sub.textContent = 'SEARCH RESULT'; }
  else { title.textContent = '文章归档'; sub.textContent = 'ALL ARTICLES'; }

  const input = document.getElementById('search-input');
  if (keyword) input.value = keyword;
  document.getElementById('search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) applyFilter({ keyword: q });
    else applyFilter({});
  });

  // 排序按钮
  document.querySelectorAll('.sort-btn').forEach(btn => {
    btn.style.fontWeight = btn.dataset.sort === sort ? '700' : '400';
    btn.style.color = btn.dataset.sort === sort ? 'var(--red)' : 'var(--gray-a)';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const p = getCurrentParams();
      p.set('sort', btn.dataset.sort);
      p.delete('page');
      applyFilter(Object.fromEntries(p));
    });
  });

  // 拦截分类和标签链接：改为原地筛选（不刷新页面）
  document.getElementById('filter-cats').addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || !a.href.includes('articles.html')) return;
    e.preventDefault();
    const u = new URL(a.href);
    applyFilter(Object.fromEntries(u.searchParams));
  });

  // 拦截文章列表内的标签链接（如果有）
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.a-tag-link');
    if (!a) return;
    e.preventDefault();
    applyFilter({ tag: a.dataset.tag });
  });

  loadCats(categoryId);
  loadArticles();
}

// 获取当前 URL 参数
function getCurrentParams() {
  return new URLSearchParams(location.search);
}

// 原地筛选：pushState + 重新加载文章（不跳转到顶部）
function applyFilter(params) {
  const sp = new URLSearchParams(params);
  // 清除空值
  sp.forEach((v, k) => { if (!v) sp.delete(k); });
  const qs = sp.toString();
  const url = 'articles.html' + (qs ? '?' + qs : '');
  history.pushState(null, '', url);

  // 更新页面状态
  const categoryId = sp.get('categoryId');
  const tag = sp.get('tag');
  const keyword = sp.get('keyword');
  const sort = sp.get('sort') || 'latest';
  curPage = 1;

  const title = document.getElementById('page-title');
  const sub = document.getElementById('page-sub');
  if (categoryId) { title.textContent = '分类文章'; sub.textContent = 'FILTERED'; }
  else if (tag) { title.textContent = '标签: ' + tag; sub.textContent = 'TAG: ' + tag.toUpperCase(); }
  else if (keyword) { title.textContent = '搜索: ' + keyword; sub.textContent = 'SEARCH RESULT'; }
  else { title.textContent = '文章归档'; sub.textContent = 'ALL ARTICLES'; }

  if (keyword) {
    document.getElementById('search-input').value = keyword;
  } else {
    document.getElementById('search-input').value = '';
  }

  // 更新排序按钮高亮
  document.querySelectorAll('.sort-btn').forEach(btn => {
    btn.style.fontWeight = btn.dataset.sort === sort ? '700' : '400';
    btn.style.color = btn.dataset.sort === sort ? 'var(--red)' : 'var(--gray-a)';
  });

  loadCats(categoryId);
  loadArticles();
}

// 回退导航（浏览器前进/后退时重新加载）
window.addEventListener('popstate', () => {
  const p = new URLSearchParams(location.search);
  const categoryId = p.get('categoryId');
  const tag = p.get('tag');
  const keyword = p.get('keyword');
  curPage = parseInt(p.get('page') || '1', 10);

  const title = document.getElementById('page-title');
  const sub = document.getElementById('page-sub');
  if (categoryId) { title.textContent = '分类文章'; }
  else if (tag) { title.textContent = '标签: ' + tag; sub.textContent = 'TAG: ' + tag.toUpperCase(); }
  else if (keyword) { title.textContent = '搜索: ' + keyword; sub.textContent = 'SEARCH RESULT'; }
  else { title.textContent = '文章归档'; sub.textContent = 'ALL ARTICLES'; }

  document.getElementById('search-input').value = keyword || '';

  const sort = p.get('sort') || 'latest';
  document.querySelectorAll('.sort-btn').forEach(btn => {
    btn.style.fontWeight = btn.dataset.sort === sort ? '700' : '400';
    btn.style.color = btn.dataset.sort === sort ? 'var(--red)' : 'var(--gray-a)';
  });

  loadCats(categoryId);
  loadArticles();
});

async function loadCats(categoryId) {
  const wrap = document.getElementById('filter-cats');
  try {
    const cats = await api.get('/api/categories');
    wrap.innerHTML = `
      <a class="cat" href="articles.html" style="--cat-bar:var(--ink)">
        <span class="idx">ALL</span><span class="name">全部</span><span class="cnt">${cats.reduce((s, c) => s + (c.articleCount || 0), 0)} posts</span><span class="bar"></span>
      </a>
      ${cats.map((c, i) => `
        <a class="cat" href="articles.html?categoryId=${c.id}" style="--cat-bar:${['var(--red)', 'var(--ink)', 'var(--gray-6)', 'var(--red)'][i % 4]}">
          <span class="idx">${String(i + 1).padStart(2, '0')}</span><span class="name">${esc(c.name)}</span><span class="cnt">${c.articleCount ?? 0} posts</span><span class="bar"></span>
        </a>`).join('')}`;
    if (categoryId) {
      wrap.querySelectorAll('.cat').forEach(a => {
        if (new URLSearchParams(a.href.split('?')[1]).get('categoryId') === categoryId) {
          a.classList.add('active');
        }
      });
    }
  } catch (e) {
    wrap.innerHTML = '<div class="empty" style="grid-column:1/13;padding:10px 0">分类加载失败</div>';
  }
}

async function loadArticles() {
  const wrap = document.getElementById('dir-list');
  wrap.innerHTML = skelEntries(10); // 骨架占位，替换转圈 loading
  try {
    const p = new URLSearchParams(location.search);
    p.set('page', curPage);
    p.set('pageSize', pageSize);
    if (!p.get('sort')) p.set('sort', 'latest');
    const data = await api.get('/api/articles?' + p.toString());

    document.getElementById('dir-total').textContent = data.total;
    if (!data.list || !data.list.length) {
      wrap.innerHTML = '<div class="empty" style="grid-column:1/13">没有找到文章</div>';
      renderPagination(0);
      return;
    }
    wrap.innerHTML = data.list.map((a, i) => `
      <a class="entry" href="article.html?id=${a.id}">
        <span class="num">${String((curPage - 1) * pageSize + i + 1).padStart(3, '0')}</span>
        <span class="title"><span>${esc(a.title)}</span><span class="arrow">→</span></span>
        <span class="entry-cat">${esc(a.category?.name || '未分类')}</span>
        <span class="date">${fmtDate(a.createdAt)}<span class="views">${fmtNum(a.viewCount)} views</span></span>
      </a>`).join('');
    renderPagination(data.total);
  } catch (e) {
    wrap.innerHTML = `<div class="empty" style="grid-column:1/13">加载失败: ${esc(e.message)}</div>`;
  }
}

function renderPagination(total) {
  const wrap = document.getElementById('pagination');
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const btn = (label, page, cls = '') => {
    const q = new URLSearchParams(location.search);
    q.set('page', page);
    return `<a href="articles.html?${q.toString()}" class="page-btn ${cls}">${label}</a>`;
  };

  let html = '';
  html += btn('←', Math.max(1, curPage - 1), curPage <= 1 ? 'disabled' : '');
  const start = Math.max(1, curPage - 2);
  const end = Math.min(totalPages, start + 4);
  for (let i = start; i <= end; i++) {
    html += i === curPage ? `<span class="cur">${i}</span>` : btn(i, i);
  }
  html += btn('→', Math.min(totalPages, curPage + 1), curPage >= totalPages ? 'disabled' : '');
  wrap.innerHTML = html;

  // 拦截分页按钮：原地跳转不刷新
  wrap.querySelectorAll('.page-btn').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const u = new URL(a.href);
      const p = Object.fromEntries(u.searchParams);
      curPage = parseInt(p.page || '1', 10);
      const q = new URLSearchParams(location.search);
      q.set('page', curPage);
      history.pushState(null, '', 'articles.html?' + q.toString());
      loadArticles();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
}
