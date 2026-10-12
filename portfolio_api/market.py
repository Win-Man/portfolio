"""市场规则：A 股代码的市场归属、资产类型判断。

**单一事实来源**。此前 assets.py 和 stocks.py 各有一份 `get_market_code`，
且规则都是"6/9 开头 → sh，其它 → sz"，这对 ETF 是错的：

    ETF 561580（央企红利ETF华泰柏瑞）→ 旧逻辑给 sz561580 → 腾讯返回 none_match
                                      正确应为 sh561580

本模块集中所有代码规则，避免再次漂移。
"""

# A 股代码首位字符 → 交易所
#   5 = 上交所基金/ETF（510xxx 50ETF、511xxx 货币、512~517xxx 行业、560~563xxx、588xxx 科创）
#   6 = 上交所主板 + 科创板（688xxx）
#   9 = 上交所 B 股
#   0 = 深交所主板
#   1 = 深交所基金/ETF（159xxx）/ 债券（10~12xxxx）
#   2 = 深交所 B 股
#   3 = 深交所创业板
_SH_PREFIXES = frozenset("569")
_SZ_PREFIXES = frozenset("0123")

# 基金类（ETF / LOF / 封闭式）代码规则
#   沪市：5 开头全部是基金
#   深市：159xxx（ETF）、16xxxx（LOF）、18xxxx（封闭式）
_SZ_FUND_PREFIXES = ("15", "16", "18")


def normalize_code(code: str) -> str:
    """剥掉可能存在的 sh/sz/大写前缀，返回纯 6 位数字代码。

    'sh561580' / 'SH561580' / '561580' → '561580'
    """
    if not code:
        return ""
    c = code.strip()
    if len(c) > 6 and c[:2].lower() in ("sh", "sz", "bj"):
        c = c[2:]
    return c.upper()


def market_prefix(code: str) -> str:
    """返回交易所前缀 'sh' / 'sz'。

    未知首位字符时默认 'sz'（与历史行为一致，但会记录在调用方日志里）。
    """
    c = normalize_code(code)
    if not c:
        return "sz"
    head = c[0]
    if head in _SH_PREFIXES:
        return "sh"
    if head in _SZ_PREFIXES:
        return "sz"
    # 4/8 开头是北交所（bj），当前未支持；7 开头是沪市配股/退市
    if head in ("4", "8"):
        return "bj"
    return "sz"


def market_code(code: str) -> str:
    """返回带市场前缀的代码，如 'sh561580'、'sz000001'。"""
    return f"{market_prefix(code)}{normalize_code(code)}"


def market_label(code: str) -> str:
    """返回中文市场名，用于前端展示。"""
    return {"sh": "沪市", "sz": "深市", "bj": "北交所"}.get(market_prefix(code), "未知")


def is_etf(code: str) -> bool:
    """判断代码是否属于 ETF / LOF / 封闭式基金（而非个股）。

    用于决定分红数据源：
      - 个股 → akshare.stock_dividend_cninfo
      - 基金 → akshare.fund_etf_dividend_sina
    """
    c = normalize_code(code)
    if not c or not c.isdigit():
        return False
    if c.startswith("5"):
        # 沪市 5 开头全部是基金
        return True
    if market_prefix(c) == "sz" and c.startswith(_SZ_FUND_PREFIXES):
        return True
    return False


def guess_asset_type(code: str) -> str:
    """从代码推断资产类型（用于手动录入时的兜底；搜索结果会带准确类型）。"""
    return "ETF" if is_etf(code) else "股票"


def asset_type_from_source(code: str, source_kind: str | None = None) -> str:
    """结合数据来源判断资产类型。

    source_kind: 'etf' / 'stock' / None（搜索来源已知时优先用它）
    """
    if source_kind == "etf":
        return "ETF"
    if source_kind == "stock":
        return "股票"
    return guess_asset_type(code)
