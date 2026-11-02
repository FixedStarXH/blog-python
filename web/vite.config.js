import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'
import { fileURLToPath } from 'url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

// 多页面(MPA)入口：前台 + 后台全部 HTML，URL 结构与原来完全一致
const pages = [
  'index.html', 'about.html', 'articles.html', 'article.html',
  'archive.html', 'tags.html', 'search.html', 'me.html', 'write.html',
  'rss.html', '404.html', 'login.html', 'register.html',
  'admin/login.html', 'admin/dashboard.html', 'admin/articles.html',
  'admin/categories.html', 'admin/tags.html', 'admin/comments.html',
  'admin/images.html', 'admin/links.html', 'admin/settings.html',
  'admin/audit.html',
]

const input = {}
for (const p of pages) {
  input[p.replace(/\.html$/, '').replace(/\//g, '_')] = resolve(__dirname, p)
}

export default defineConfig({
  // 相对路径：页面在 /web/ 或 / 下都能正常加载资源
  base: './',
  plugins: [react()],
  build: {
    // 输出到博客后端托管目录 dist/（router.go 的 webDir）
    outDir: resolve(__dirname, '../dist'),
    emptyOutDir: true,
    rollupOptions: { input },
  },
})
