import React, { useCallback, useEffect, useState } from 'react'
import { Clock3, ExternalLink, GitFork, RefreshCw, Search, SlidersHorizontal, Star, X } from 'lucide-react'
import { api } from '../../lib/api'
import type { Repo } from '../../types'

const LANGS = ['', 'Python', 'TypeScript', 'Go', 'Rust', 'Java']
const PERIODS = [{ label: '今日', value: 1 }, { label: '本周', value: 7 }, { label: '本月', value: 30 }]

function RepoCard({ repo }: { repo: Repo }) {
  const href = repo.html_url || repo.url || '#'
  return (
    <article className="explore-repo">
      <div className="explore-repo__topline">
        <span className="explore-repo__rank">REPO</span>
        {repo.trending_period && <span className="explore-repo__period">{repo.trending_period}</span>}
      </div>
      <a href={href} target="_blank" rel="noreferrer" className="explore-repo__name">
        {repo.full_name}<ExternalLink size={13} />
      </a>
      <p className="explore-repo__description">{repo.description || '暂无仓库描述'}</p>
      <div className="explore-repo__metrics">
        <span><Star size={13} />{(repo.stars ?? 0).toLocaleString()}</span>
        <span><GitFork size={13} />{(repo.forks ?? 0).toLocaleString()}</span>
        {repo.stars_today ? <span className="explore-repo__growth">+{repo.stars_today.toLocaleString()} stars</span> : null}
        {repo.language && <span className="explore-repo__language">{repo.language}</span>}
      </div>
      {(repo.topics ?? []).length > 0 && (
        <div className="explore-repo__topics">
          {(repo.topics ?? []).slice(0, 4).map(topic => <span key={topic}>{topic}</span>)}
        </div>
      )}
    </article>
  )
}

export function ExploreView() {
  const [period, setPeriod] = useState(7)
  const [lang, setLang] = useState('')
  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<'trending' | 'search'>('trending')

  const loadTrending = useCallback(async () => {
    setLoading(true)
    setError('')
    try { setRepos(await api.getTrending(period, lang)) }
    catch (err) { setRepos([]); setError(err instanceof Error ? err.message : '热榜暂时不可用') }
    finally { setLoading(false) }
  }, [lang, period])

  useEffect(() => {
    if (mode === 'trending') void loadTrending()
  }, [loadTrending, mode])

  function handleSearch(event: React.FormEvent) {
    event.preventDefault()
    const nextQuery = query.trim()
    if (!nextQuery) return
    setMode('search')
    setLoading(true)
    setError('')
    api.searchRepos(nextQuery, lang)
      .then(setRepos)
      .catch(err => { setRepos([]); setError(err instanceof Error ? err.message : '搜索暂时不可用') })
      .finally(() => setLoading(false))
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
      </section>

      <section className="explore-filters">
        <div className="explore-filter-group">
          <span>时间范围</span>
          {PERIODS.map(item => <button type="button" key={item.value} onClick={() => setPeriod(item.value)} className={period === item.value ? 'is-active' : ''}>{item.label}</button>)}
        </div>
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
          <div className="explore-state explore-state--error"><strong>暂时无法读取 GitHub</strong><span>{error}</span><button type="button" onClick={() => void loadTrending()}><RefreshCw size={14} />重试</button></div>
        ) : repos.length === 0 ? (
          <div className="explore-state"><strong>{mode === 'search' ? '没有匹配结果' : '暂无趋势数据'}</strong><span>{mode === 'search' ? '换一个关键词或移除语言筛选试试。' : '稍后刷新，或切换语言范围。'}</span></div>
        ) : (
          <div className="explore-grid">{repos.map(repo => <RepoCard key={repo.full_name} repo={repo} />)}</div>
        )}
      </section>
    </div>
  )
}
