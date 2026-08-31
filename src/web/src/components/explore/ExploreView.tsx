import React, { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, Clock3, Download, ExternalLink, GitFork, RefreshCw, Search, SlidersHorizontal, Star, X } from 'lucide-react'
import { api } from '../../lib/api'
import { languageColor } from '../../lib/githubColors'
import { formatRelativeTime } from '../../lib/time'
import type { Repo } from '../../types'

const LANGS = ['', 'Python', 'TypeScript', 'Go', 'Rust', 'Java']
const PERIODS = [{ label: '今日', value: 1 }, { label: '本周', value: 7 }, { label: '本月', value: 30 }]

// 发现 preset：中文意图 → 英文限定词（提升中文库命中，GitHub 按 README 匹配）
const PRESETS: Array<{ label: string; query: string; hint: string }> = [
  { label: 'agent 技能', query: 'claude code skill', hint: 'agentskills agent skill' },
  { label: 'LLM harness', query: 'llm harness', hint: 'agent framework runtime' },
  { label: 'AI 编程助手', query: 'ai coding assistant', hint: 'coding agent ide' },
]

// 会话级结果缓存：切换视图再回来不重复拉取（key = 模式|筛选|查询）
const exploreCache = new Map<string, Repo[]>()

function cacheKey(mode: string, period: number, lang: string, query: string) {
  return `${mode}|${period}|${lang}|${query}`
}

function formatStars(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(value)
}

function RepoCard({ repo, isLocal, onBring, onOpenDetail }: {
  repo: Repo
  isLocal: boolean
  onBring: (repo: Repo) => void
  onOpenDetail: (repo: Repo) => void
}) {
  const href = repo.html_url || repo.url || '#'
  return (
    <article className="explore-repo" onClick={() => onOpenDetail(repo)} role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter') onOpenDetail(repo) }}>
      <div className="explore-repo__topline">
        {repo.owner_avatar
          ? <img className="explore-repo__avatar" src={repo.owner_avatar} alt="" loading="lazy" />
          : <span className="explore-repo__avatar explore-repo__avatar--fallback">{repo.full_name.charAt(0).toUpperCase()}</span>}
        <a href={href} target="_blank" rel="noreferrer" className="explore-repo__name" onClick={e => e.stopPropagation()}>
          {repo.full_name}<ExternalLink size={12} />
        </a>
        {isLocal && <span className="explore-repo__local"><Check size={11} />已导入</span>}
      </div>
      <p className="explore-repo__description">{repo.description || '暂无仓库描述'}</p>
      {(repo.topics ?? []).length > 0 && (
        <div className="explore-repo__topics">
          {(repo.topics ?? []).slice(0, 4).map(topic => <button key={topic} type="button" className="explore-topic" onClick={e => { e.stopPropagation(); }}>{topic}</button>)}
          {(repo.topics ?? []).length > 4 && <span className="explore-topic__more">+{(repo.topics ?? []).length - 4}</span>}
        </div>
      )}
      <div className="explore-repo__metrics">
        <span><Star size={12} />{formatStars(repo.stars ?? 0)}</span>
        <span><GitFork size={12} />{formatStars(repo.forks ?? 0)}</span>
        {repo.stars_today ? <span className="explore-repo__growth">+{formatStars(repo.stars_today)} stars{repo.trending_period === 'daily' ? '今日' : repo.trending_period === 'weekly' ? '本周' : '本月'}</span> : null}
        {repo.language && <span className="explore-repo__language"><i style={{ background: languageColor(repo.language) }} />{repo.language}</span>}
        {repo.license && <span className="explore-repo__license">{repo.license}</span>}
        {typeof repo.open_issues === 'number' && repo.open_issues > 0 && <span>{repo.open_issues} issues</span>}
        {repo.pushed_at && <span className="explore-repo__time"><Clock3 size={11} />{formatRelativeTime(repo.pushed_at)}</span>}
      </div>
      {!isLocal && (
        <button type="button" className="explore-repo__bring" onClick={e => { e.stopPropagation(); onBring(repo) }}>
          <Download size={13} />带回工作区
        </button>
      )}
    </article>
  )
}

function RepoDrawer({ repo, onClose }: { repo: Repo | null; onClose: () => void }) {
  const [readme, setReadme] = useState<string | null>(null)
  const [readmeLoading, setReadmeLoading] = useState(false)
  useEffect(() => {
    setReadme(null)
    if (!repo) return
    const [owner, name] = repo.full_name.split('/')
    setReadmeLoading(true)
    api.getRepoReadme(owner, name)
      .then(data => setReadme(data.readme))
      .catch(() => setReadme(null))
      .finally(() => setReadmeLoading(false))
  }, [repo?.full_name])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    if (repo) document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [repo, onClose])

  if (!repo) return null
  return (
    <div className="explore-drawer" role="dialog" aria-modal="true" aria-label={`${repo.full_name} 详情`}>
      <button type="button" className="explore-drawer__backdrop" onClick={onClose} aria-label="关闭详情" />
      <aside className="explore-drawer__panel">
        <header className="explore-drawer__head">
          {repo.owner_avatar
            ? <img src={repo.owner_avatar} alt="" />
            : <span className="explore-repo__avatar explore-repo__avatar--fallback">{repo.full_name.charAt(0).toUpperCase()}</span>}
          <div>
            <strong>{repo.full_name}</strong>
            <small>{repo.description || '暂无描述'}</small>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={16} /></button>
        </header>
        <div className="explore-drawer__facts">
          <div><small>Stars</small><strong>{(repo.stars ?? 0).toLocaleString()}</strong></div>
          <div><small>Forks</small><strong>{(repo.forks ?? 0).toLocaleString()}</strong></div>
          {repo.stars_today ? <div><small>期间新增</small><strong>+{(repo.stars_today).toLocaleString()}</strong></div> : null}
          {repo.language && <div><small>语言</small><strong><i style={{ background: languageColor(repo.language), display: 'inline-block', width: 9, height: 9, borderRadius: '50%', marginRight: 5 }} />{repo.language}</strong></div>}
          {repo.license && <div><small>许可</small><strong>{repo.license}</strong></div>}
          {typeof repo.open_issues === 'number' && <div><small>Open issues</small><strong>{repo.open_issues}</strong></div>}
          {repo.pushed_at && <div><small>最近更新</small><strong>{formatRelativeTime(repo.pushed_at)}</strong></div>}
        </div>
        {(repo.topics ?? []).length > 0 && (
          <div className="explore-drawer__topics">
            {(repo.topics ?? []).map(topic => <span key={topic}>{topic}</span>)}
          </div>
        )}
        <section className="explore-drawer__readme">
          {readmeLoading ? <p className="muted">正在读取 README…</p> : readme ? <pre>{readme}</pre> : <p className="muted">没有 README 或读取失败。</p>}
        </section>
        <footer className="explore-drawer__actions">
          <a href={repo.html_url || repo.url} target="_blank" rel="noreferrer"><ExternalLink size={13} />打开 GitHub</a>
        </footer>
      </aside>
    </div>
  )
}

export function ExploreView({ onOpenProjects }: { onOpenProjects?: () => void }) {
  const [period, setPeriod] = useState(7)
  const [lang, setLang] = useState('')
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [searchedQuery, setSearchedQuery] = useState('')
  const [mode, setMode] = useState<'trending' | 'search'>('trending')
  const [localRepos, setLocalRepos] = useState<Set<string>>(new Set())
  const [bringBusy, setBringBusy] = useState('')
  const [bringMessage, setBringMessage] = useState('')
  const [selectedRepo, setSelectedRepo] = useState<Repo | null>(null)

  // 本机已导入项目名集合（用于卡片显示「已导入」状态）
  useEffect(() => {
    api.getProjects()
      .then(projects => setLocalRepos(new Set(projects.map(project => {
        const parts = project.workspace_root.replace(/\\/g, '/').split('/')
        return parts[parts.length - 1] || ''
      }).filter(Boolean))))
      .catch(() => {})
  }, [])

  async function bringBack(repo: Repo) {
    const url = repo.html_url || repo.url || ''
    if (!url || bringBusy) return
    setBringBusy(repo.full_name); setBringMessage('')
    try {
      await api.importGithub(url)
      const name = repo.full_name.split('/').pop() || ''
      setBringMessage(`「${name}」已克隆到工作区。打开项目工作台选择下一步（体检 / 跑起来 / 导读 / 验证）。`)
      setLocalRepos(current => new Set(current).add(name))
    } catch (err) {
      setBringMessage(err instanceof Error ? err.message : '导入失败')
    } finally {
      setBringBusy('')
    }
  }

  const loadTrending = useCallback(async (force = false) => {
    const key = cacheKey(mode, period, lang, '')
    const cached = exploreCache.get(key)
    if (cached && !force) {
      setRepos(cached)
      setError('')
      return
    }
    setLoading(true)
    setError('')
    try {
      const next = await api.getTrending(period, lang)
      exploreCache.set(key, next)
      setRepos(next)
    } catch (err) { setRepos([]); setError(err instanceof Error ? err.message : '热榜暂时不可用') }
    finally { setLoading(false) }
  }, [lang, period, mode])

  useEffect(() => {
    if (mode === 'trending') void loadTrending()
  }, [loadTrending, mode])

  function handleSearch(event: React.FormEvent) {
    event.preventDefault()
    const nextQuery = query.trim()
    if (!nextQuery) return
    setMode('search')
    setSearchedQuery(nextQuery)
    runSearch(nextQuery, '')
  }

  function runSearch(nextQuery: string, hint: string) {
    const key = cacheKey('search', period, lang, `${nextQuery}|${hint}`)
    const cached = exploreCache.get(key)
    if (cached) {
      setRepos(cached)
      setError('')
      return
    }
    setLoading(true)
    setError('')
    api.searchRepos(nextQuery, lang, hint)
      .then(next => { exploreCache.set(key, next); setRepos(next) })
      .catch(err => { setRepos([]); setError(err instanceof Error ? err.message : '搜索暂时不可用') })
      .finally(() => setLoading(false))
  }

  function applyPreset(preset: { label: string; query: string; hint: string }) {
    setMode('search')
    setQuery(preset.query)
    setSearchedQuery(preset.label)
    runSearch(preset.query, preset.hint)
  }

  // 错误重试：趋势榜重拉，搜索模式重发上次关键词
  function retry() {
    if (mode === 'search' && searchedQuery) {
      const key = cacheKey('search', period, lang, `${searchedQuery}|`)
      exploreCache.delete(key)
      setLoading(true); setError('')
      api.searchRepos(searchedQuery, lang, '')
        .then(next => { exploreCache.set(key, next); setRepos(next) })
        .catch(err => { setRepos([]); setError(err instanceof Error ? err.message : '搜索暂时不可用') })
        .finally(() => setLoading(false))
      return
    }
    void loadTrending(true)
  }

  function resetSearch() {
    setQuery('')
    setMode('trending')
  }

  return (
    <div className="explore-view">
      <header className="explore-header">
        <div>
          <div className="explore-eyebrow"><SlidersHorizontal size={13} /> GITHUB DISCOVERY</div>
          <h1>探索值得带回本地的仓库</h1>
          <p>从趋势和搜索开始，把感兴趣的代码带进工作区继续分析。</p>
        </div>
      </header>

      <section className="explore-search-shell">
        <form onSubmit={handleSearch} className="explore-search">
          <Search size={16} />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索仓库、技术栈或主题" aria-label="搜索 GitHub 仓库" />
          {query && <button type="button" onClick={() => setQuery('')} title="清空搜索" aria-label="清空搜索"><X size={14} /></button>}
          <button type="submit" className="explore-search__submit"><Search size={14} /><span>搜索</span></button>
        </form>
        <div className="explore-toolbar">
          <div className="explore-segmented" role="tablist" aria-label="探索模式">
            <button type="button" role="tab" aria-selected={mode === 'trending'} onClick={() => setMode('trending')} className={mode === 'trending' ? 'is-active' : ''}>趋势榜</button>
            <button type="button" role="tab" aria-selected={mode === 'search'} onClick={() => setMode('search')} className={mode === 'search' ? 'is-active' : ''}>搜索结果</button>
          </div>
          {mode === 'search' && <button type="button" className="explore-reset" onClick={resetSearch}><RefreshCw size={13} />重置</button>}
        </div>
        <div className="explore-presets" aria-label="发现推荐">
          <span>发现：</span>
          {PRESETS.map(preset => (
            <button key={preset.label} type="button" onClick={() => applyPreset(preset)}>{preset.label}</button>
          ))}
        </div>
      </section>

      <section className="explore-filters">
        {mode === 'trending' && (
          <div className="explore-filter-group">
            <span>时间范围</span>
            {PERIODS.map(item => <button type="button" key={item.value} onClick={() => setPeriod(item.value)} className={period === item.value ? 'is-active' : ''}>{item.label}</button>)}
          </div>
        )}
        <div className="explore-filter-group">
          <span>语言</span>
          {LANGS.map(item => <button type="button" key={item} onClick={() => setLang(item)} className={lang === item ? 'is-active' : ''}>{item || '全部'}</button>)}
        </div>
      </section>

      <section className="explore-results">
        <div className="explore-results__heading"><div><h2>{mode === 'trending' ? '正在上升' : '匹配仓库'}</h2><span>{loading ? '正在同步 GitHub…' : `${repos.length} 个结果`}</span></div><Clock3 size={15} /></div>
        {loading ? (
          <div className="explore-grid">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="explore-skeleton" />)}</div>
        ) : error ? (
          <div className="explore-state explore-state--error"><strong>暂时无法读取 GitHub</strong><span>{error}</span><button type="button" onClick={retry}><RefreshCw size={14} />重试</button></div>
        ) : repos.length === 0 ? (
          <div className="explore-state"><strong>{mode === 'search' ? '没有匹配结果' : '暂无趋势数据'}</strong><span>{mode === 'search' ? '换一个关键词或移除语言筛选试试。' : '稍后刷新，或切换语言范围。'}</span></div>
        ) : (
          <div className="explore-grid">{repos.map(repo => <RepoCard key={repo.full_name} repo={repo} isLocal={localRepos.has(repo.full_name.split('/').pop() || '')} onBring={bringBack} onOpenDetail={setSelectedRepo} />)}</div>
        )}
      </section>
      {bringBusy && <div className="explore-bring"><span className="explore-bring__spinner" />正在克隆 {bringBusy} 到工作区…</div>}
      {bringMessage && (
        <div className="explore-bring explore-bring--done">
          <span>{bringMessage}</span>
          {!bringBusy && onOpenProjects && <button type="button" className="explore-bring__open" onClick={onOpenProjects}>打开项目工作台 →</button>}
        </div>
      )}
      <RepoDrawer repo={selectedRepo} onClose={() => setSelectedRepo(null)} />
    </div>
  )
}
