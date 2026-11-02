/* ============================================================
   API 封装 — 统一请求 / 鉴权 / 错误处理 / 双 token 自动刷新
   基于接口文档契约:{ code, message, data }
   ============================================================ */

// 清除全部登录凭证（access + refresh + 用户缓存）
function clearAuth() {
  localStorage.removeItem('accessToken');
  localStorage.removeItem('refreshToken');
  localStorage.removeItem('admin');
}

// 迁移清理：单 token 时代遗留的旧键（后端已不识别），避免与双 token 混淆
localStorage.removeItem('token');

// refreshing 缓存刷新 Promise：多个请求同时 401 时，只发一次刷新请求，其余等待同一个
let refreshing = null;

// 用 refresh token 换新双 token；返回是否成功
// 失败（refresh 过期/被轮换）→ 前端清凭证回登录页
function tryRefresh() {
  const refreshToken = localStorage.getItem('refreshToken');
  if (!refreshToken) return Promise.resolve(false);
  if (!refreshing) {
    refreshing = fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refreshToken }),
    })
      .then(r => r.json())
      .then(json => {
        if (json.code === 200) {
          localStorage.setItem('accessToken', json.data.accessToken);
          localStorage.setItem('refreshToken', json.data.refreshToken);
          return true;
        }
        return false;
      })
      .catch(() => false)
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

// 统一请求
// noRefresh = true：该接口的 401 是业务失败（如登录密码错），不触发 token 刷新重放
async function request(method, url, data, noRefresh) {
  // 包装成可重放的请求体：401 且刷新成功后，用同一份参数重放一次
  const doFetch = async (retried) => {
    const opts = { method, headers: {} };
    const token = localStorage.getItem('accessToken');
    if (token) opts.headers['Authorization'] = 'Bearer ' + token;

    if (data !== undefined) {
      if (data instanceof FormData) {
        opts.body = data; // 不设 Content-Type,浏览器自动带 boundary
      } else {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(data);
      }
    }

    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error('网络请求失败,请检查后端是否启动');
    }

    let json;
    try {
      json = await res.json();
    } catch (e) {
      throw new Error('服务返回异常(' + res.status + ')');
    }

    // 401 且还没重试过：先尝试刷新 token，成功则重放原请求
    if (json.code === 401 && !retried && !noRefresh) {
      const ok = await tryRefresh();
      if (ok) return doFetch(true);
      // 刷新也失败 → 登录态彻底失效：清凭证回登录页
      clearAuth();
      if (location.pathname.includes('admin')) location.href = 'login.html';
      else if (!/login\.html|register\.html/.test(location.pathname)) location.href = 'login.html';
      throw new Error(json.message || '登录已过期，请重新登录');
    }

    if (json.code !== 200) {
      throw new Error(json.message || '请求失败');
    }
    return json.data;
  };
  return doFetch(false);
}

// 快捷方法
const api = {
  get: (url) => request('GET', url),
  post: (url, data, noRefresh) => request('POST', url, data, noRefresh),
  put: (url, data) => request('PUT', url, data),
  del: (url) => request('DELETE', url),
};

// 挂到 window：React 组件(ESM)与页面内联脚本均通过 window.api 访问
window.api = api;

/* ---------- SSE 流式请求（AI 润色 / AI 问答 打字机效果） ----------
   后端统一输出：data: {"delta":"文字"} … data: [DONE]
   本函数负责：带 token 的 POST → 读流逐行解析 → onDelta 增量回调 → onDone 完成回调
   401 处理与 request() 一致：自动刷新 token 重放一次 */
async function streamRequest(url, data, onDelta, onDone) {
  const doStream = async (retried) => {
    const token = localStorage.getItem('accessToken');
    const opts = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    };
    if (token) opts.headers['Authorization'] = 'Bearer ' + token;

    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error('网络请求失败，请检查网络或稍后再试');
    }

    // 401：先刷新重放一次，再失败就清凭证
    if (res.status === 401 && !retried) {
      const ok = await tryRefresh();
      if (ok) return doStream(true);
      clearAuth();
      throw new Error('登录已过期，请重新登录');
    }

    // 非流式响应 = 业务错误（如"AI 未配置"），读出 message
    if (!res.ok || !(res.headers.get('content-type') || '').includes('text/event-stream')) {
      let msg = '请求失败';
      try { const j = await res.json(); msg = j.message || msg; } catch (e) { /* 忽略 */ }
      throw new Error(msg);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const finish = () => { onDone && onDone(); };
    while (true) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (e) {
        throw new Error('连接中断');
      }
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') { finish(); return; }
        try {
          const j = JSON.parse(payload);
          if (j.delta) onDelta(j.delta);
        } catch (e) { /* 非 JSON 行忽略 */ }
      }
    }
    finish();
  };
  return doStream(false);
}

/* ---------- 格式化工具 ---------- */
function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())}`;
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtNum(n) {
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(Math.round(n));
}

function fmtMinutes(content) {
  if (!content) return 1;
  const words = content.replace(/<[^>]*>/g, ' ').replace(/\s+/g, '').length;
  return Math.max(1, Math.ceil(words / 300));
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ---------- Toast 提示 ---------- */
function toast(msg, isErr) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------- 通用查询参数 ---------- */
function getParam(name) {
  return new URLSearchParams(location.search).get(name);
}

/* ---------- 主题切换 ---------- */
function applyTheme(dark) {
  document.body.classList.toggle('dark', dark);
  localStorage.setItem('theme', dark ? 'dark' : 'light');
  const btn = document.querySelector('.theme-btn');
  if (btn) btn.textContent = dark ? '☀' : '☾';
}
function initTheme() {
  const saved = localStorage.getItem('theme');
  applyTheme(saved === 'dark');
}
