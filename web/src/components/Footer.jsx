/**
 * 页脚组件：DOM 结构与原 common.js 的 renderFooter 完全一致
 */
export default function Footer() {
  return (
    <div className="grid">
      <div className="copy">
        © 2026 <b>BLOG-SYSTEM</b> — Built with Go + Gin + Swiss
      </div>
      <div className="links">
        <a href="about.html">关于</a>
        <a href="rss.html">RSS</a>
        <a href="register.html">注册</a>
        <a href="admin/login.html">后台</a>
      </div>
    </div>
  )
}
