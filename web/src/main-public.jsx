/**
 * 前台公共布局 React 入口（MPA 多页面共用一个入口）
 *
 * 在 <header id="topbar"> / <footer id="footer"> 容器中挂载 React 组件，
 * 视觉结构与原 renderTopbar/renderFooter 完全一致（CSS 原样复用）。
 * 搜索弹层通过 Portal 挂到 document.body，保持 DOM 位置与原来相同。
 *
 * 依赖：页面已先加载 api.js / common.js（普通脚本），
 * 本模块通过 window.api / window.esc 等全局对象复用其能力。
 */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import Topbar from './components/Topbar.jsx'
import Footer from './components/Footer.jsx'
import SearchOverlay from './components/SearchOverlay.jsx'

function App() {
  const [searchOpen, setSearchOpen] = useState(false)
  // 导航高亮：initPage(activeName) 已写入 body[data-active-nav]
  const active = document.body.dataset.activeNav || null

  return (
    <>
      <Topbar active={active} onSearch={() => setSearchOpen(true)} />
      <SearchOverlay
        open={searchOpen}
        onOpen={() => setSearchOpen(true)}
        onClose={() => setSearchOpen(false)}
      />
    </>
  )
}

// 顶栏与页脚分别挂载到各自的容器，避免 Footer 被渲染进 #topbar 导致页脚跑到页面顶部
const topbarEl = document.getElementById('topbar')
if (topbarEl) {
  createRoot(topbarEl).render(<App />)
}
const footerEl = document.getElementById('footer')
if (footerEl) {
  createRoot(footerEl).render(<Footer />)
}
