"""代理绕过（net.py）的单元测试。

背景：本机常设 HTTP_PROXY=127.0.0.1:xxxx，部分行情域名走代理会连不通
（实测 82.push2.eastmoney.com → ProxyError），表现为「搜不到股票 / 拉不到分红」。
这组用例锁住三件事：
  1. NO_PROXY 只**追加**不覆盖，用户自己的配置不能丢；
  2. 幂等 —— 多次调用不会把域名重复堆成 `a.com,a.com,a.com`；
  3. 语义真的生效 —— 交给 requests 自己的判断函数验证，而不是只看字符串。
"""
import os

import pytest
import requests

import net


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """恢复环境变量 + 复位幂等标记（ensure_setup 直接写 os.environ，monkeypatch 不管）。"""
    saved = {
        k: os.environ.get(k)
        for k in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY")
    }
    monkeypatch.setattr(net, "_applied", False)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def bypass_on(monkeypatch):
    monkeypatch.setattr(net.settings, "net_bypass_proxy", True)
    monkeypatch.setattr(net.settings, "no_proxy_hosts", "")
    return net.settings


def test_merged_no_proxy_keeps_existing_entries(monkeypatch, bypass_on):
    """已有的 NO_PROXY 项必须原样保留在前面。"""
    monkeypatch.setenv("NO_PROXY", "corp.internal,10.0.0.0/8")
    value = net.merged_no_proxy()
    assert value.startswith("corp.internal,10.0.0.0/8")
    assert "eastmoney.com" in value
    assert "qt.gtimg.cn" in value


def test_ensure_setup_writes_both_cases(monkeypatch, bypass_on):
    """大小写两个变量都要写：urllib 认小写，部分库只读大写。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    value = net.ensure_setup()
    assert os.environ["NO_PROXY"] == value
    assert os.environ["no_proxy"] == value
    assert "eastmoney.com" in value


def test_ensure_setup_is_idempotent(monkeypatch, bypass_on):
    """重复调用（启动 + 每次取 client_kwargs）不能累积重复域名。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    net.ensure_setup()
    first = os.environ["NO_PROXY"]
    net.ensure_setup(force=True)
    net.ensure_setup(force=True)
    assert os.environ["NO_PROXY"] == first
    assert os.environ["NO_PROXY"].count("eastmoney.com") == 1


def test_extra_hosts_from_config(monkeypatch, bypass_on):
    """NO_PROXY_HOSTS 追加的域名同样生效，且允许写 .x.com / *.x.com 形式。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(net.settings, "no_proxy_hosts", "my-quote.example,*.foo.cn")
    value = net.ensure_setup()
    assert "my-quote.example" in value
    assert "foo.cn" in value


def test_bypass_disabled_leaves_env_untouched(monkeypatch):
    """NET_BYPASS_PROXY=false 时完全回到旧行为，不碰环境变量。"""
    monkeypatch.setattr(net.settings, "net_bypass_proxy", False)
    monkeypatch.setenv("NO_PROXY", "only-this")
    assert net.ensure_setup() == "only-this"
    assert os.environ["NO_PROXY"] == "only-this"


def test_client_kwargs_disables_env_proxy(bypass_on):
    """绕过模式：显式 trust_env=False，行情请求一律直连。"""
    kwargs = net.client_kwargs(timeout=7.0)
    assert kwargs["trust_env"] is False
    assert kwargs["timeout"] == 7.0


def test_client_kwargs_keeps_env_when_disabled(monkeypatch):
    """关闭绕过时不加 trust_env，httpx 恢复读环境代理（旧行为）。"""
    monkeypatch.setattr(net.settings, "net_bypass_proxy", False)
    kwargs = net.client_kwargs(timeout=10.0)
    assert "trust_env" not in kwargs


def test_requests_really_bypasses_market_hosts(monkeypatch, bypass_on):
    """交给 requests 自己的判断函数验证：行情域名绕过、其它域名仍走代理。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    net.ensure_setup()

    market_urls = [
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        "https://qt.gtimg.cn/q=sz000858",
        "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
        "https://fund.eastmoney.com/513530.html",
        "http://www.cninfo.com.cn/new/hisAnnouncement/query",
    ]
    for url in market_urls:
        assert requests.utils.should_bypass_proxies(url, None) is True, url

    # 非行情域名不受影响（仍然按系统代理走）
    assert requests.utils.should_bypass_proxies("https://example.com/api", None) is False


def test_normalize_hosts_dedupes_and_strips(monkeypatch):
    hosts = net.normalize_hosts([" Eastmoney.com ", ".eastmoney.com", "*.foo.cn", "", None, "EASTMONEY.COM"])
    assert hosts == ["eastmoney.com", "foo.cn"]
