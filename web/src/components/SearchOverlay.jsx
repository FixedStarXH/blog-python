/**
 * 全局搜索弹层组件（React 重写 common.js 的 initSearchOverlay）
 *
 * 通过 Portal 挂到 document.body，DOM 结构 / class 与原版完全一致，
 * 行为逐条对齐：/ 快捷键唤起、Esc 关闭、遮罩点击关闭、输入防抖实时建议、
 * 回车跳转搜索页、搜索历史(localStorage)与热门标签快捷入口。
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'

// 复用全局工具（common.js / api.js 中的普通脚本函数）
const esc = (s) => window.esc(s)
const fmtDate = (iso) => window.fmtDate(iso)
const highlightKeyword = (t, k) => window.highlightKeyword(t, k)
const getSearchHistory = () => window.getSearchHistory()
const saveSearchHistory = (q) => window.saveSearchHistory(q)

export default function SearchOverlay({ open, onOpen, onClose }) {
  const [query, setQuery] = useState('')
  // results: null=未搜索(显示提示区) ｜ [] ｜ 数组 ｜ 'error'
  const [results, setResults] = useState(null)
  const [total, setTotal] = useState(0)
  const [history, setHistory] = useState([])
  const [hotTags, setHotTags] = useState([])
  const inputRef = useRef(null)
  const debounceRef = useRef(null)
  const hotLoadedRef = useRef(false)

  // 加载热门标签（只加载一次，等价原 dataset.loaded 标记）
  const loadHotTags = useCallback(async () => {
    if (hotLoadedRef.current) return
    hotLoadedRef.current = true
    try {
      const tags = await window.api.get('/api/tags')
      const sorted = (tags || [])
        .sort((a, b) => (b.articleCount || 0) - (a.articleCount || 0))
        .slice(0, 10)
      setHotTags(sorted)
    } catch {
      setHotTags([])
    }
  }, [])

  // 打开/关闭：锁背景滚动、重置状态、聚焦输入框
  useEffect(() => {
    if (open) {
      setQuery('')
      setResults(null)
      setHistory(getSearchHistory())
      loadHotTags()
      document.body.style.overflow = 'hidden'
      setTimeout(() => inputRef.current?.focus(), 50)
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [open, loadHotTags])

  // 全局快捷键 / ：非输入框内按 / 唤起搜索
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '/') return
      const tag = (e.target.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return
      e.preventDefault()
      onOpen()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onOpen])

  // 防抖实时搜索建议（250ms）
  const suggest = async (q) => {
    if (!q) {
      setResults(null)
      return
    }
    try {
      const data = await window.api.get(
        '/api/articles?keyword=' + encodeURIComponent(q) + '&page=1&pageSize=5&sort=latest'
      )
      setResults(data.list || [])
      setTotal(data.total || 0)
    } catch {
      setResults('error')
    }
  }

  const onInput = (e) => {
    const val = e.target.value
    setQuery(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => suggest(val.trim()), 250)
  }

  // 回车 → 跳完整搜索页
  const submit = () => {
    const q = query.trim()
    if (q) {
      saveSearchHistory(q)
      location.href = 'search.html?q=' + encodeURIComponent(q)
      onClose()
    }
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter') submit()
    else if (e.key === 'Escape') onClose()
  }

  const clearHistory = () => {
    window.clearSearchHistory()
    setHistory([])
  }

  // 点击遮罩关闭
  const onMaskClick = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  const showHint = results === null
  const keyword = query.trim()

  return createPortal(
    <div
      className={open ? 'search-overlay show' : 'search-overlay'}
      id="search-overlay"
      onClick={onMaskClick}
    >
      <div className="search-modal">
        <div className="search-modal-bar">
          <span className="search-modal-ico">⌕</span>
          <input
            ref={inputRef}
            type="text"
            id="search-modal-input"
            placeholder="搜索文章标题 / 正文…"
            autoComplete="off"
            value={query}
            onChange={onInput}
            onKeyDown={onKeyDown}
          />
          <kbd>Esc</kbd>
        </div>
        <div className="search-modal-body" id="search-modal-body">
          {showHint ? (
            <div className="search-hint">
              {history.length > 0 && (
                <div className="search-hint-section" id="search-history-section">
                  <div className="search-hint-title">
                    最近搜索 <button className="search-hint-clear" onClick={clearHistory}>清空</button>
                  </div>
                  <div className="search-tags" id="search-history-list">
                    {history.map((q) => (
                      <a key={q} className="tag-item" href={'search.html?q=' + encodeURIComponent(q)}>
                        # {esc(q)}
                      </a>
                    ))}
                  </div>
                </div>
              )}
              <div className="search-hint-section">
                <div className="search-hint-title">热门标签</div>
                <div className="search-tags" id="search-hot-tags">
                  {hotTags.length
                    ? hotTags.map((t) => (
                        <a key={t.name} className="tag-item" href={'articles.html?tag=' + encodeURIComponent(t.name)}>
                          {esc(t.name)} <span className="n">{t.articleCount || 0}</span>
                        </a>
                      ))
                    : null}
                </div>
              </div>
            </div>
          ) : (
            <div className="search-results" id="search-results">
              {results === 'error' ? (
                <div className="search-empty">搜索失败，请重试</div>
              ) : !results.length ? (
                <div className="search-empty">没有匹配的文章，按回车查看全部结果 →</div>
              ) : (
                <>
                  <div className="search-results-count">约 {total} 条结果</div>
                  {results.map((a) => (
                    <a className="search-result-item" href={'article.html?id=' + a.id} key={a.id}>
                      <span className="sr-cat">{esc(a.category?.name || '未分类')}</span>
                      <span
                        className="sr-title"
                        dangerouslySetInnerHTML={{ __html: highlightKeyword(esc(a.title), keyword) }}
                      />
                      <span className="sr-date">{fmtDate(a.createdAt)}</span>
                    </a>
                  ))}
                  <div className="search-results-more" onClick={submit}>查看全部结果 →</div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}
