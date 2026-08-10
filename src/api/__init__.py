"""
GitHub API 搜索模块

提供GitHub仓库搜索、详情获取、趋势分析等功能。
"""

from .github_client import (
    GitHubClient,
    RepoInfo,
    StarHistory,
    GitHubAPIError,
    RateLimitError,
    NotFoundError,
    search_repos_sync,
    get_repo_details_sync,
)

from .trending import (
    TrendingAnalyzer,
    TrendingRepo,
    TrendingAnalysis,
    TrendingReportGenerator,
    get_trending,
    analyze_growth,
    get_trending_sync,
)

__all__ = [
    # 客户端
    "GitHubClient",
    "RepoInfo",
    "StarHistory",
    # 错误类
    "GitHubAPIError",
    "RateLimitError",
    "NotFoundError",
    # 趋势分析
    "TrendingAnalyzer",
    "TrendingRepo",
    "TrendingAnalysis",
    "TrendingReportGenerator",
    # 便捷函数
    "search_repos_sync",
    "get_repo_details_sync",
    "get_trending",
    "analyze_growth",
    "get_trending_sync",
]
