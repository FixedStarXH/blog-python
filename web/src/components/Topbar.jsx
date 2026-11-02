/**
 * 顶部导航组件
 * DOM 结构 / class / id 与原 common.js 的 renderTopbar 完全一致，
 * 交互（主题切换、搜索唤起、移动端菜单）改为 React 状态驱动。
 */
import { useState } from 'react'

// 读取登录用户（localStorage 缓存，与 getStoredAdmin 一致）
function getStoredAdmin() {
  try {
    return JSON.parse(localStorage.getItem('admin') || 'null')
  } catch {
    return null
  }
}

export default function Topbar({ active, onSearch }) {
  // 主题图标：浅色显示 ☾（点击进入夜间），深色显示 ☀（点击回到白天）
  const [dark, setDark] = useState(() => document.body.classList.contains('dark'))
  const [menuOpen, setMenuOpen] = useState(false)
  const [admin] = useState(getStoredAdmin)

  const token = localStorage.getItem('accessToken')
  const navLink = (key, href, label, idx = '00') => (
    <a href={href} data-nav={key} className={key === active ? 'active' : undefined}>
      <i>{idx}</i> {label}
    </a>
  )

  const toggleTheme = () => {
    const next = !dark
    setDark(next)
    window.applyTheme(next)
  }

  // 点击导航链接后自动收起移动端菜单（事件委托到 nav，只处理 <a>）
  const onNavClick = (e) => {
    if (e.target.closest('a')) setMenuOpen(false)
  }

  return (
    <div className="grid">
      <a className="logo" href="index.html">
        <span className="logo-mark">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <rect x="2.5" y="2.5" width="19" height="19" rx="5" stroke="currentColor" strokeWidth="2" />
            <circle cx="12" cy="12" r="3.5" fill="#E53012" />
          </svg>
        </span>
        BLOG<sup>®</sup>
      </a>

      <nav className={menuOpen ? 'nav open' : 'nav'} id="nav-menu" onClick={onNavClick}>
        {navLink('home', 'index.html', '首页', '01')}
        {navLink('archive', 'articles.html', '文章', '02')}
        {navLink('tags', 'tags.html', '标签', '03')}
        {admin && admin.role >= 2 && navLink('admin', 'admin/dashboard.html', '管理', '◎')}
        {token ? (
          <>
            {navLink('me', 'me.html', admin?.nickname || '我的')}
            <a href="javascript:;" data-nav="logout" onClick={() => window.doLogout()}>
              <i>00</i> 退出
            </a>
          </>
        ) : (
          <>
            {navLink('login', 'login.html', '登录')}
            {navLink('register', 'register.html', '注册')}
          </>
        )}
        <button
          className="search-trigger"
          id="search-trigger"
          title="搜索 (快捷键 /)"
          aria-label="搜索"
          onClick={onSearch}
        >
          ⌕
        </button>
        <button className="theme-btn" title="切换夜间模式" onClick={toggleTheme}>
          {dark ? '☀' : '☾'}
        </button>
      </nav>

      <button
        className={menuOpen ? 'nav-toggle active' : 'nav-toggle'}
        id="nav-toggle"
        aria-label="菜单"
        title="展开导航"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen(!menuOpen)}
      >
        <span></span>
        <span></span>
        <span></span>
      </button>
    </div>
  )
}
