"""
GitHub 趋势分析模块

分析GitHub项目的星数增长趋势，识别飙升项目，提供趋势排名。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from .github_client import GitHubClient, RepoInfo, StarHistory


@dataclass
class TrendingRepo:
    """趋势项目数据类"""
    repo: RepoInfo
    current_stars: int
    stars_gained: int
    growth_rate: float  # 每日增长数
    growth_percentage: float  # 增长百分比
    period_days: int
    trend_score: float  # 趋势分数，用于排序
    history: Optional[StarHistory] = None

    @property
    def is_surge(self) -> bool:
        """判断是否为飙升项目"""
        # 飙升标准: 每日增长超过100星 或 增长率超过20%
        return self.growth_rate > 100 or self.growth_percentage > 20

    @property
    def trend_level(self) -> str:
        """趋势等级"""
        if self.growth_rate > 500:
            return "爆火"
        elif self.growth_rate > 200:
            return "热门"
        elif self.growth_rate > 50:
            return "上升"
        elif self.growth_rate > 10:
            return "关注"
        else:
            return "平稳"


@dataclass
class TrendingAnalysis:
    """趋势分析结果"""
    period: str
    repos: List[TrendingRepo]
    total_repos_analyzed: int
    analysis_time: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def surge_repos(self) -> List[TrendingRepo]:
        """获取飙升项目"""
        return [r for r in self.repos if r.is_surge]

    @property
    def top_gainers(self) -> List[TrendingRepo]:
        """获取增长最多的项目"""
        return sorted(self.repos, key=lambda x: x.stars_gained, reverse=True)[:10]

    @property
    def fastest_growing(self) -> List[TrendingRepo]:
        """获取增长最快的项目"""
        return sorted(self.repos, key=lambda x: x.growth_rate, reverse=True)[:10]


class TrendingAnalyzer:
    """
    GitHub 趋势分析器

    分析项目星数增长趋势，识别热门和飙升项目。

    使用示例:
        analyzer = TrendingAnalyzer()
        async with analyzer:
            # 获取最近7天的趋势项目
            trending = await analyzer.get_trending_repos(period="7d")

            # 分析特定项目的增长
            growth = await analyzer.analyze_star_growth("torvalds", "linux")
    """

    # 时间周期映射
    PERIOD_MAP = {
        "1d": 1,
        "7d": 7,
        "30d": 30,
        "90d": 90,
        "180d": 180,
        "1y": 365,
    }

    # 热门项目分类查询
    CATEGORY_QUERIES = {
        "trending": "stars:>1000 pushed:>{date}",
        "new": "created:>{date} stars:>100",
        "rising": "stars:100..10000 pushed:>{date}",
    }

    def __init__(
        self,
        token: Optional[str] = None,
        client: Optional[GitHubClient] = None,
    ):
        """
        初始化趋势分析器

        Args:
            token: GitHub Token
            client: 现有的GitHub客户端（可选）
        """
        self.token = token
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        if self._owns_client:
            self._client = GitHubClient(token=self.token)
            await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self._owns_client and self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
            self._client = None

    def _parse_period(self, period: str) -> int:
        """
        解析时间周期字符串

        Args:
            period: 周期字符串 (1d, 7d, 30d, 90d, 180d, 1y)

        Returns:
            天数
        """
        period = period.lower().strip()
        if period in self.PERIOD_MAP:
            return self.PERIOD_MAP[period]

        # 尝试解析数字+单位格式
        try:
            if period.endswith("d"):
                return int(period[:-1])
            elif period.endswith("w"):
                return int(period[:-1]) * 7
            elif period.endswith("m"):
                return int(period[:-1]) * 30
            elif period.endswith("y"):
                return int(period[:-1]) * 365
            else:
                return int(period)
        except ValueError:
            raise ValueError(f"Invalid period format: {period}. Use '7d', '30d', '1y', etc.")

    async def get_trending_repos(
        self,
        query: str = "",
        language: Optional[str] = None,
        period: str = "7d",
        category: str = "trending",
        per_page: int = 30,
        include_history: bool = False,
    ) -> TrendingAnalysis:
        """
        获取热门趋势项目

        Args:
            query: 额外搜索关键词
            language: 编程语言过滤
            period: 时间周期
            category: 类别 (trending, new, rising)
            per_page: 结果数量
            include_history: 是否包含星数历史（会消耗更多API配额）

        Returns:
            趋势分析结果
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use 'async with TrendingAnalyzer() as analyzer:' pattern")

        days = self._parse_period(period)
        date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 构建查询
        base_query = self.CATEGORY_QUERIES.get(category, self.CATEGORY_QUERIES["trending"])
        base_query = base_query.format(date=date)

        if query:
            full_query = f"{query} {base_query}"
        else:
            full_query = base_query

        # 搜索项目
        repos = await self._client.search_repos(
            query=full_query,
            language=language,
            sort="stars",
            order="desc",
            per_page=per_page,
        )

        # 计算趋势数据
        trending_repos = []
        for repo in repos:
            trending_repo = await self._calculate_trend(repo, days, include_history)
            trending_repos.append(trending_repo)

        # 按趋势分数排序
        trending_repos.sort(key=lambda x: x.trend_score, reverse=True)

        return TrendingAnalysis(
            period=period,
            repos=trending_repos,
            total_repos_analyzed=len(repos),
            analysis_time=datetime.now().isoformat(),
            metadata={
                "query": full_query,
                "language": language,
                "category": category,
            }
        )

    async def _calculate_trend(
        self,
        repo: RepoInfo,
        days: int,
        include_history: bool = False,
    ) -> TrendingRepo:
        """
        计算单个项目的趋势数据

        Args:
            repo: 仓库信息
            days: 分析天数
            include_history: 是否获取历史数据

        Returns:
            趋势项目数据
        """
        history = None
        stars_gained = 0

        # 尝试获取星数历史
        if include_history:
            try:
                history = await self._client.get_star_history(repo.owner, repo.name, days=days)
                stars_gained = history.total_growth
            except Exception:
                # 如果获取历史失败，使用估算
                stars_gained = self._estimate_stars_gained(repo, days)
        else:
            stars_gained = self._estimate_stars_gained(repo, days)

        # 计算增长率
        growth_rate = stars_gained / days if days > 0 else 0
        growth_percentage = (stars_gained / max(repo.stars - stars_gained, 1)) * 100

        # 计算趋势分数
        # 综合考虑：绝对增长数(40%) + 增长率(30%) + 当前星数(30%)
        trend_score = (
            stars_gained * 0.4 +
            growth_rate * 100 * 0.3 +
            repo.stars * 0.001 * 0.3
        )

        return TrendingRepo(
            repo=repo,
            current_stars=repo.stars,
            stars_gained=stars_gained,
            growth_rate=growth_rate,
            growth_percentage=growth_percentage,
            period_days=days,
            trend_score=trend_score,
            history=history,
        )

    def _estimate_stars_gained(self, repo: RepoInfo, days: int) -> int:
        """
        估算项目星数增长（基于项目年龄和当前星数）

        这是一个粗略的估算，实际增长数据需要调用Stargazers API。

        Args:
            repo: 仓库信息
            days: 分析天数

        Returns:
            估算的增长星数
        """
        try:
            created = datetime.fromisoformat(repo.created_at.replace("Z", "+00:00")).replace(tzinfo=None)
            age_days = (datetime.now() - created).days

            if age_days <= 0:
                return 0

            # 计算平均每日增长
            avg_daily = repo.stars / age_days

            # 最近的项目增长通常更快
            if age_days < 30:
                multiplier = 3.0
            elif age_days < 90:
                multiplier = 2.0
            elif age_days < 365:
                multiplier = 1.5
            else:
                multiplier = 1.0

            estimated = int(avg_daily * days * multiplier)
            return min(estimated, repo.stars)  # 不能超过总星数

        except Exception:
            return 0

    async def analyze_star_growth(
        self,
        owner: str,
        repo: str,
        days: int = 90,
    ) -> TrendingRepo:
        """
        分析特定项目的星数增长

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            days: 分析天数

        Returns:
            趋势分析数据
        """
        if not self._client:
            raise RuntimeError("Client not initialized")

        # 获取仓库详情
        repo_info = await self._client.get_repo_details(owner, repo)

        # 获取星数历史
        history = await self._client.get_star_history(owner, repo, days=days)

        # 计算趋势
        return await self._calculate_trend(repo_info, days, include_history=True)

    async def compare_repos(
        self,
        repos: List[Tuple[str, str]],
        days: int = 30,
    ) -> List[TrendingRepo]:
        """
        比较多个项目的增长趋势

        Args:
            repos: 仓库列表 [(owner, repo), ...]
            days: 分析天数

        Returns:
            趋势数据列表（按增长排序）
        """
        if not self._client:
            raise RuntimeError("Client not initialized")

        tasks = [
            self.analyze_star_growth(owner, repo, days)
            for owner, repo in repos
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results = [r for r in results if isinstance(r, TrendingRepo)]
        valid_results.sort(key=lambda x: x.stars_gained, reverse=True)

        return valid_results

    async def get_language_trending(
        self,
        language: str,
        period: str = "7d",
        per_page: int = 20,
    ) -> TrendingAnalysis:
        """
        获取特定语言的趋势项目

        Args:
            language: 编程语言
            period: 时间周期
            per_page: 结果数量

        Returns:
            趋势分析结果
        """
        return await self.get_trending_repos(
            language=language,
            period=period,
            per_page=per_page,
        )

    async def get_new_repos_trending(
        self,
        days: int = 7,
        language: Optional[str] = None,
        min_stars: int = 50,
        per_page: int = 20,
    ) -> TrendingAnalysis:
        """
        获取新建项目的趋势

        Args:
            days: 天数
            language: 编程语言
            min_stars: 最小星数
            per_page: 结果数量

        Returns:
            趋势分析结果
        """
        date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = f"created:>{date} stars:>{min_stars}"

        return await self.get_trending_repos(
            query=query,
            language=language,
            period=f"{days}d",
            per_page=per_page,
        )


class TrendingReportGenerator:
    """趋势报告生成器"""

    @staticmethod
    def generate_text_report(analysis: TrendingAnalysis) -> str:
        """
        生成文本格式的趋势报告

        Args:
            analysis: 趋势分析结果

        Returns:
            格式化的文本报告
        """
        lines = []
        lines.append(f"GitHub 趋势报告 - {analysis.period}")
        lines.append("=" * 60)
        lines.append(f"分析时间: {analysis.analysis_time}")
        lines.append(f"分析项目数: {analysis.total_repos_analyzed}")
        lines.append("")

        # 飙升项目
        surge_repos = analysis.surge_repos
        if surge_repos:
            lines.append("🔥 飙升项目")
            lines.append("-" * 40)
            for repo in surge_repos[:5]:
                lines.append(f"  {repo.repo.full_name}")
                lines.append(f"    星数: {repo.current_stars:,} (+{repo.stars_gained:,})")
                lines.append(f"    日均增长: {repo.growth_rate:.1f}")
                lines.append(f"    趋势: {repo.trend_level}")
                lines.append("")

        # 增长最多
        lines.append("📈 增长最多")
        lines.append("-" * 40)
        for repo in analysis.top_gainers[:10]:
            lines.append(f"  {repo.repo.full_name}: +{repo.stars_gained:,} ⭐")

        lines.append("")

        # 增长最快
        lines.append("🚀 增长最快")
        lines.append("-" * 40)
        for repo in analysis.fastest_growing[:10]:
            lines.append(f"  {repo.repo.full_name}: {repo.growth_rate:.1f}/天")

        return "\n".join(lines)

    @staticmethod
    def generate_markdown_report(analysis: TrendingAnalysis) -> str:
        """
        生成Markdown格式的趋势报告

        Args:
            analysis: 趋势分析结果

        Returns:
            Markdown格式的报告
        """
        lines = []
        lines.append(f"# GitHub 趋势报告 - {analysis.period}")
        lines.append("")
        lines.append(f"> 分析时间: {analysis.analysis_time}")
        lines.append(f"> 分析项目数: {analysis.total_repos_analyzed}")
        lines.append("")

        # 飙升项目
        surge_repos = analysis.surge_repos
        if surge_repos:
            lines.append("## 飙升项目")
            lines.append("")
            lines.append("| 项目 | 星数 | 增长 | 日均 | 趋势 |")
            lines.append("|------|------|------|------|------|")
            for repo in surge_repos[:10]:
                lines.append(
                    f"| [{repo.repo.full_name}]({repo.repo.html_url}) "
                    f"| {repo.current_stars:,} "
                    f"| +{repo.stars_gained:,} "
                    f"| {repo.growth_rate:.1f} "
                    f"| {repo.trend_level} |"
                )
            lines.append("")

        # 排行榜
        lines.append("## 增长排行榜")
        lines.append("")
        lines.append("| 排名 | 项目 | 语言 | 星数 | 增长 | 描述 |")
        lines.append("|------|------|------|------|------|------|")
        for i, repo in enumerate(analysis.repos[:20], 1):
            desc = (repo.repo.description or "")[:50]
            if len(repo.repo.description or "") > 50:
                desc += "..."
            lines.append(
                f"| {i} "
                f"| [{repo.repo.full_name}]({repo.repo.html_url}) "
                f"| {repo.repo.language or '-'} "
                f"| {repo.current_stars:,} "
                f"| +{repo.stars_gained:,} "
                f"| {desc} |"
            )

        return "\n".join(lines)

    @staticmethod
    def generate_json_data(analysis: TrendingAnalysis) -> Dict[str, Any]:
        """
        生成JSON格式的趋势数据

        Args:
            analysis: 趋势分析结果

        Returns:
            JSON数据字典
        """
        return {
            "period": analysis.period,
            "analysis_time": analysis.analysis_time,
            "total_repos": analysis.total_repos_analyzed,
            "metadata": analysis.metadata,
            "repos": [
                {
                    "name": repo.repo.full_name,
                    "owner": repo.repo.owner,
                    "repo": repo.repo.name,
                    "description": repo.repo.description,
                    "language": repo.repo.language,
                    "stars": repo.current_stars,
                    "stars_gained": repo.stars_gained,
                    "growth_rate": round(repo.growth_rate, 2),
                    "growth_percentage": round(repo.growth_percentage, 2),
                    "trend_score": round(repo.trend_score, 2),
                    "trend_level": repo.trend_level,
                    "is_surge": repo.is_surge,
                    "url": repo.repo.html_url,
                    "topics": repo.repo.topics,
                }
                for repo in analysis.repos
            ],
            "summary": {
                "surge_count": len(analysis.surge_repos),
                "top_gainers": [
                    {"name": r.repo.full_name, "gained": r.stars_gained}
                    for r in analysis.top_gainers[:5]
                ],
                "fastest_growing": [
                    {"name": r.repo.full_name, "rate": round(r.growth_rate, 2)}
                    for r in analysis.fastest_growing[:5]
                ],
            }
        }


# 便捷函数
async def get_trending(
    language: Optional[str] = None,
    period: str = "7d",
    token: Optional[str] = None,
    per_page: int = 30,
) -> TrendingAnalysis:
    """
    获取GitHub热门项目（便捷函数）

    Args:
        language: 编程语言
        period: 时间周期
        token: GitHub Token
        per_page: 结果数量

    Returns:
        趋势分析结果
    """
    async with TrendingAnalyzer(token=token) as analyzer:
        return await analyzer.get_trending_repos(
            language=language,
            period=period,
            per_page=per_page,
        )


async def analyze_growth(
    owner: str,
    repo: str,
    days: int = 90,
    token: Optional[str] = None,
) -> TrendingRepo:
    """
    分析项目增长（便捷函数）

    Args:
        owner: 仓库所有者
        repo: 仓库名称
        days: 分析天数
        token: GitHub Token

    Returns:
        趋势分析数据
    """
    async with TrendingAnalyzer(token=token) as analyzer:
        return await analyzer.analyze_star_growth(owner, repo, days)


def get_trending_sync(
    language: Optional[str] = None,
    period: str = "7d",
    token: Optional[str] = None,
) -> TrendingAnalysis:
    """
    同步获取GitHub热门项目

    Args:
        language: 编程语言
        period: 时间周期
        token: GitHub Token

    Returns:
        趋势分析结果
    """
    return asyncio.run(get_trending(language=language, period=period, token=token))


if __name__ == "__main__":
    # 测试代码
    async def test():
        async with TrendingAnalyzer() as analyzer:
            # 获取7天趋势
            print("=== 获取7天热门项目 ===")
            trending = await analyzer.get_trending_repos(period="7d", per_page=10)

            # 生成文本报告
            report = TrendingReportGenerator.generate_text_report(trending)
            print(report)

            print("\n=== 分析特定项目增长 ===")
            growth = await analyzer.analyze_star_growth("facebook", "react", days=30)
            print(f"项目: {growth.repo.full_name}")
            print(f"当前星数: {growth.current_stars:,}")
            print(f"30天增长: +{growth.stars_gained:,}")
            print(f"日均增长: {growth.growth_rate:.1f}")
            print(f"趋势等级: {growth.trend_level}")

    asyncio.run(test())
