/* ============================================================
   后台共享 — 鉴权检查 / 侧栏渲染 / 分页 / 工具
   ============================================================ */

// 鉴权:无 access token 跳登录
function requireAuth() {
  const token = localStorage.getItem('accessToken');
  if (!token) { location.href = 'login.html'; return null; }
  return token;
}

// 读取当前管理员
function getAdmin() {
  try { return JSON.parse(localStorage.getItem('admin') || 'null'); }
  catch (e) { return null; }
}

// 渲染侧栏
function renderSidebar(active) {
  const items = [
    { key: 'dashboard', href: 'dashboard.html', i: '01', icon: '▦', name: '仪表盘' },
    { key: 'articles', href: 'articles.html', i: '02', icon: '▤', name: '文章管理' },
    { key: 'categories', href: 'categories.html', i: '03', icon: '▧', name: '分类管理' },
    { key: 'tags', href: 'tags.html', i: '04', icon: '#', name: '标签管理' },
    { key: 'comments', href: 'comments.html', i: '05', icon: '✎', name: '评论管理' },
    { key: 'images', href: 'images.html', i: '06', icon: '▨', name: '图库上传' },
    { key: 'links', href: 'links.html', i: '07', icon: '↗', name: '友情链接' },
    { key: 'audit', href: 'audit.html', i: '08', icon: '≡', name: '审计日志' },
    { key: 'settings', href: 'settings.html', i: '09', icon: '⚙', name: '站点设置' },
  ];
  const admin = getAdmin();
  document.getElementById('sidebar').innerHTML = `
    <div class="brand"><span class="tag"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><rect x="2.5" y="2.5" width="19" height="19" rx="5" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="3.5" fill="#E53012"/></svg></span><span>BLOG·ADMIN</span></div>
    <nav class="nav">
      ${items.map(it => `
        <a href="${it.href}" class="${it.key === active ? 'active' : ''}">
          <i>${it.i}</i><span>${it.name}</span>
        </a>`).join('')}
    </nav>
    <div class="user">
      <div class="name">${esc(admin ? (admin.nickname || admin.username || '管理员') : '管理员')}</div>
      <span class="logout" onclick="logout()">退出登录 →</span>
      <a class="to-blog" href="../index.html">← 返回博客</a>
    </div>`;
}

// 退出：先吊销 refresh token，再清本地凭证
async function logout() {
  try {
    const rt = localStorage.getItem('refreshToken');
    if (rt) await api.post('/api/auth/logout', { refreshToken: rt });
  } catch (e) { /* 忽略：网络失败也继续本地登出 */ }
  clearAuth();
  location.href = 'login.html';
}

// 通用分页渲染
function renderPager(el, page, totalPages, cb) {
  let html = `<button ${page <= 1 ? 'class="disabled"' : ''} onclick="pagerGo(${page - 1})">←</button>`;
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, start + 4);
  for (let i = start; i <= end; i++) {
    html += i === page ? `<span class="cur">${i}</span>` : `<button onclick="pagerGo(${i})">${i}</button>`;
  }
  html += `<button ${page >= totalPages ? 'class="disabled"' : ''} onclick="pagerGo(${page + 1})">→</button>`;
  el.innerHTML = html;
  window.pagerGo = (p) => { if (p < 1 || p > totalPages) return; cb(p); };
}

// 状态徽标
function statusBadge(status) {
  switch (status) {
    case 1: return '<span class="badge green">已发布</span>';
    case 0: return '<span class="badge gray">草稿</span>';
    case 2: return '<span class="badge orange">待审核</span>';
    case 3: return '<span class="badge red">已驳回</span>';
    case 4: return '<span class="badge blue">已排期</span>';
    default: return '<span class="badge gray">未知</span>';
  }
}
function commentStatusBadge(status) {
  switch (status) {
    case 1: return '<span class="badge green">已通过</span>';
    case 0: return '<span class="badge orange">待审核</span>';
    case 2: return '<span class="badge red">已驳回</span>';
    default: return '<span class="badge gray">未知</span>';
  }
}

// 初始化后台页面
function initAdmin(active) {
  requireAuth();
  renderSidebar(active);
  initRipple();
}

// 点击涟漪：后台按钮事件委托（与前台效果一致）
function initRipple() {
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn');
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

// 表格骨架行：生成 n 行占位（用于后台列表加载）
function skeletonRows(n, cols = 9) {
  let rows = '';
  for (let i = 0; i < n; i++) {
    let tds = '';
    for (let j = 0; j < cols; j++) {
      tds += '<td class="skel-cell"><div class="skel-line" style="width:' + (45 + ((i * 13 + j * 7) % 40)) + '%"></div></td>';
    }
    rows += '<tr>' + tds + '</tr>';
  }
  return rows;
}
