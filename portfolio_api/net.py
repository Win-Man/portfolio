"""行情请求的代理绕过（NO_PROXY）配置。

## 为什么需要

开发机/公司网络常设 `HTTP_PROXY=http://127.0.0.1:xxxx`（Clash、Surge 之类）。
`akshare`（底层用 requests）和 `httpx` 默认都会走它，但部分行情域名经代理会
连不通 —— 实测本机 `82.push2.eastmoney.com` 直接 `ProxyError`。

症状看起来完全是业务 bug：搜不到股票、分红拉不到、日K 为空、只能降级成
「沿用上次缓存」。实际上是网络层被代理挡住，日志里只有一行 warning。

## 两层处理

1. **环境变量**（`ensure_setup()`）：把行情域名合并进 `NO_PROXY` / `no_proxy`。
   这是 akshare 唯一可用的开关 —— 它自己建 Session，没有 proxy 参数可传。
2. **显式构造参数**（`client_kwargs()`）：本项目自己的 httpx 客户端传
   `trust_env=False`，根本不读环境代理。比依赖 NO_PROXY 的字符串匹配更确定，
   也不会被不同库的大小写/通配符解析差异坑到。

想反过来走代理（例如海外网络访问国内行情源），把 `NET_BYPASS_PROXY` 设为
`false`，两层同时关闭，行为回到改动前。
"""
import logging
import os
import re
from typing import Iterable, List

from config import settings

logger = logging.getLogger(__name__)

# 内置行情域名清单。用**裸域名**（不带前导点、不带 *）：
#   requests → parsed.hostname.endswith(host)，裸域名同时命中自身与所有子域
#   httpx    → 裸域名被解析成 all://*host，同样覆盖子域
# 加前导点反而只匹配子域、漏掉域名本身。
MARKET_HOSTS: tuple[str, ...] = (
    # 腾讯：实时行情 / 日K
    "qt.gtimg.cn",
    "gtimg.cn",
    "ifzq.gtimg.cn",
    "proxy.finance.qq.com",
    # 新浪：ETF 分红、基金列表
    "sinajs.cn",
    "sina.com.cn",
    # 东方财富：全量行情备用源、分红页面、数据中心
    "eastmoney.com",
    # 巨潮资讯：个股分红
    "cninfo.com.cn",
    # 交易所
    "sse.com.cn",
    "szse.cn",
    # akshare 其它常用源
    "10jqka.com.cn",
    "hexun.com",
    "xueqiu.com",
    # 本机（前端 / 内置服务之间不要绕代理）
    "localhost",
    "127.0.0.1",
)

_applied = False


def normalize_hosts(hosts: Iterable[str]) -> List[str]:
    """去空白、去空项、去重（大小写不敏感），保持首次出现的顺序。"""
    out: List[str] = []
    seen = set()
    for raw in hosts:
        host = (raw or "").strip()
        if not host:
            continue
        # 允许写 ".eastmoney.com" / "*.eastmoney.com"，统一存成裸域名
        host = host.lstrip("*").lstrip(".").lower()
        if not host or host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out


def bypass_hosts() -> List[str]:
    """内置清单 + `NO_PROXY_HOSTS` 配置追加。"""
    extra = re.split(r"[,\s]+", settings.no_proxy_hosts or "")
    return normalize_hosts([*MARKET_HOSTS, *extra])


def _read_no_proxy() -> str:
    """读现有的 NO_PROXY（大小写都认，与 urllib 一致）。"""
    return os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""


def merged_no_proxy() -> str:
    """现有 NO_PROXY 在前 + 行情域名在后，去重保序（不丢用户自己的配置）。"""
    existing = [p.strip() for p in _read_no_proxy().split(",") if p.strip()]
    seen = {p.lstrip("*").lstrip(".").lower() for p in existing if p.strip() != "*"}
    out = list(existing)
    for host in bypass_hosts():
        if host not in seen:
            out.append(host)
            seen.add(host)
    return ",".join(out)


def ensure_setup(force: bool = False) -> str:
    """把行情域名并进 NO_PROXY（幂等）。返回最终的 NO_PROXY 值。

    akshare / requests 每次请求都会重读环境变量，所以进程启动时设一次就够，
    后台任务（观察池快照）也一并生效。
    """
    global _applied
    if _applied and not force:
        return _read_no_proxy()

    _applied = True
    if not settings.net_bypass_proxy:
        logger.info("NET_BYPASS_PROXY=false：行情请求沿用系统代理设置")
        return _read_no_proxy()

    value = merged_no_proxy()
    # 大小写两个都写：urllib/requests 认 no_proxy，部分库只读大写
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    logger.info("行情请求已绕过代理：NO_PROXY=%s", value)
    return value


def client_kwargs(timeout: float = 10.0) -> dict:
    """本项目 httpx 客户端的构造参数。

    绕过模式下显式 `trust_env=False`：不读 HTTP(S)_PROXY，也不依赖 NO_PROXY
    的字符串匹配 —— 行情请求一律直连，行为不随 shell 环境漂移。
    """
    ensure_setup()
    if settings.net_bypass_proxy:
        return {"timeout": timeout, "trust_env": False}
    return {"timeout": timeout}
