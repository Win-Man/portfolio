"""每日日报邮件：把当天的日报渲染成一封 HTML 邮件并发出。

## 一封，不是两封

用户要的是「每天的日报」，而不是「每天收两封分别讲组合和观察池的信」。
所以这里把**当天全部组合日报 + 观察池日报**合成一封：标题里就能看到两边的
今日盈亏，正文按「组合一 / 组合二 / 观察池」分节。哪天没生成组合日报，
就只发观察池那一节；两份都没有 → 抛 `NoReportsError`，不发空信。

## 邮件客户端兼容性决定了代码长什么样

邮件客户端（Outlook / 网易邮箱大师 / QQ 邮箱 / Gmail）对 CSS 的支持停留在
十几年前：**没有 flex、没有 grid、`<style>` 可能被整段删掉**。所以：

- 全部用 `<table role="presentation">` 搭布局，单元格上写内联 `style`；
- 不用 class、不用外链字体、不用 `position` / `float`；
- 定宽 720px 且 `width="100%"`，窄屏客户端自动缩放而不是横向滚动；
- 图表用「一行表格 + 宽度百分比的单元格」画条形，而不是 canvas / svg。

这些都是「在浏览器里好看」和「在邮箱里能看」之间的必要妥协。

## 渲染与发送分离

- `render_daily_email(payloads, report_date)` 是纯函数，不碰网络、不碰数据库，
  单测直接断言它生成的 HTML；
- `smtp_send(message, config)` 是**唯一**的 I/O，测试替换它就能验证整条链路。

## 口径不做第二套实现

今日盈亏 / 持仓盈亏 / 达标信号全部来自 `daily_reports.py` 落库的 payload，
这里只负责「怎么说」，不重算任何一个数字 —— 否则邮件和页面迟早对不上。
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from config import settings
from daily_reports import report_payload
from models import DailyReport

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

# A 股惯例：红涨绿跌（与欧美相反）
COLOR_UP = "#d93025"
COLOR_DOWN = "#0f9d58"
COLOR_FLAT = "#5b6472"
COLOR_TEXT = "#1f2430"
COLOR_MUTED = "#8a93a3"
COLOR_BORDER = "#e6e9ef"
COLOR_BRAND = "#2f6fed"

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif"
)

DASH = "—"
MAX_ITEMS_IN_MAIL = 60      # 极端情况下（几百只标的）截断，邮件不是数据库导出


# ========== 异常 ==========

class MailNotConfigured(RuntimeError):
    """缺少 SMTP 账号 / 授权码 / 收件人。文案直接给用户看。"""


class NoReportsError(RuntimeError):
    """当天没有任何日报可发。"""


class MailSendError(RuntimeError):
    """SMTP 层失败（连不上、认证失败、被拒收）。"""


# ========== 格式化（纯字符串，不做四舍五入以外的计算） ==========

def _num(value) -> Optional[float]:
    """安全转 float；bool / None / NaN / inf → None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def money(value) -> str:
    """金额（不带符号）：¥96,201.00 / —"""
    n = _num(value)
    return DASH if n is None else f"¥{n:,.2f}"


def signed_money(value) -> str:
    """带符号金额：+¥711.50 / -¥711.50 / ¥0.00 / —

    四舍五入到 0 的负数（ETF 每股跌 0.003 元）显示 `¥0.00` 而不是 `-¥0.00`：
    后者看起来像是个 bug。
    """
    n = _num(value)
    if n is None:
        return DASH
    rounded = round(n, 2)
    if abs(rounded) < 0.005:
        return "¥0.00"
    sign = "+" if rounded > 0 else "-"
    return f"{sign}¥{abs(rounded):,.2f}"


def percent(value, digits: int = 2, signed: bool = True) -> str:
    """百分比：+0.74% / -0.74% / 0.00% / —"""
    n = _num(value)
    if n is None:
        return DASH
    sign = ""
    if signed and n > 0:
        sign = "+"
    elif signed and n < 0:
        sign = "-"
    return f"{sign}{abs(n):,.{digits}f}%"


def price(value, asset_type=None) -> str:
    """价格：股票 2 位、ETF / 基金 3 位（与页面 priceDigits 一致）。"""
    n = _num(value)
    if n is None:
        return DASH
    digits = 2 if (not asset_type or asset_type == "股票") else 3
    return f"{n:,.{digits}f}"


def tone_color(value) -> str:
    """涨跌配色：正红 / 负绿 / 0 与缺失灰。"""
    n = _num(value)
    if n is None or n == 0:
        return COLOR_FLAT
    return COLOR_UP if n > 0 else COLOR_DOWN


def esc(value) -> str:
    """所有进 HTML 的文本都必须过这里 —— 标的名称来自用户输入。"""
    return html_lib.escape("" if value is None else str(value), quote=True)


# ========== HTML 零件 ==========

def _td(
    content: str,
    *,
    align: str = "left",
    color: str = COLOR_TEXT,
    bold: bool = False,
    size: int = 12,
    width: Optional[str] = None,
    bg: Optional[str] = None,
    pad: str = "7px 8px",
    border: bool = True,
) -> str:
    style = (
        f"padding:{pad};font-size:{size}px;line-height:1.55;text-align:{align};"
        f"color:{color};font-family:{FONT};"
    )
    if border:
        style += f"border-bottom:1px solid {COLOR_BORDER};"
    if bold:
        style += "font-weight:600;"
    if bg:
        style += f"background-color:{bg};"
    attr = f' width="{width}"' if width else ""
    return f'<td{attr} style="{style}">{content}</td>'


def _th(label: str, *, align: str = "left", width: Optional[str] = None) -> str:
    style = (
        f"padding:7px 8px;font-size:11px;line-height:1.55;text-align:{align};"
        f"color:{COLOR_MUTED};background-color:#f7f9fc;font-family:{FONT};"
        f"border-bottom:1px solid {COLOR_BORDER};"
    )
    attr = f' width="{width}"' if width else ""
    return f'<th{attr} style="{style}">{esc(label)}</th>'


def _row(cells: Sequence[str]) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


def _table(head: Sequence[str], body: Sequence[str]) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="border-collapse:collapse;font-family:{FONT};">'
        f"<thead>{_row(head)}</thead><tbody>{''.join(body)}</tbody></table>"
    )


def _bar(pct_value, color: str = COLOR_BRAND) -> str:
    """邮件里的条形图：外层灰轨 + 内层按百分比定宽的单元格。

    纯 table 实现 —— 邮件客户端对 `div` + `width:%` 的支持反而没有 table 稳。
    """
    ratio = min(100.0, max(0.0, _num(pct_value) or 0.0))
    if ratio <= 0:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="border-collapse:collapse;"><tr>'
        f'<td width="{ratio:.1f}%" bgcolor="{color}" '
        'style="height:6px;line-height:6px;font-size:0;border-radius:3px;">&nbsp;</td>'
        '<td style="height:6px;line-height:6px;font-size:0;">&nbsp;</td>'
        "</tr></table>"
    )


def _kpi_grid(items: Sequence[Tuple[str, str, str, str]]) -> str:
    """3 列 × N 行的指标卡。每项 = (标签, 主数字, 副文案, 数字颜色)。"""
    rows: List[str] = []
    for start in range(0, len(items), 3):
        cells: List[str] = []
        for label, value, sub, color in items[start : start + 3]:
            sub_html = (
                f'<div style="font-size:11px;line-height:1.6;color:{COLOR_MUTED};">{sub}</div>'
                if sub
                else ""
            )
            cells.append(
                '<td width="33%" valign="top" '
                f'style="padding:10px 12px;border:1px solid {COLOR_BORDER};border-radius:6px;'
                f'background-color:#fcfdff;font-family:{FONT};">'
                f'<div style="font-size:11px;line-height:1.6;color:{COLOR_MUTED};">{esc(label)}</div>'
                f'<div style="font-size:17px;font-weight:600;line-height:1.5;color:{color};'
                f'font-variant-numeric:tabular-nums;">{value}</div>'
                f"{sub_html}</td>"
            )
        while len(cells) < 3:
            cells.append('<td width="33%" style="padding:0;"></td>')
        rows.append(_row(cells))

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="6" '
        f'border="0" style="border-collapse:separate;border-spacing:6px;font-family:{FONT};">'
        f"{''.join(rows)}</table>"
    )


def _section_title(title: str, note: str = "") -> str:
    note_html = (
        f'<div style="font-size:11px;line-height:1.6;color:{COLOR_MUTED};">{note}</div>'
        if note
        else ""
    )
    return (
        '<div style="margin:26px 0 8px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="padding:0 0 8px;border-bottom:2px solid {COLOR_BORDER};font-family:{FONT};">'
        f'<span style="font-size:15px;font-weight:600;color:{COLOR_TEXT};">{esc(title)}</span>'
        f"{note_html}</td></tr></table></div>"
    )


def _notes_block(notes: Sequence[str]) -> str:
    if not notes:
        return ""
    lis = "".join(
        f'<li style="margin:0 0 4px;line-height:1.7;">{esc(n)}</li>' for n in notes
    )
    return (
        f'<div style="margin:12px 0 0;padding:10px 14px;background-color:#fbfcfe;'
        f'border-left:3px solid #c9d4e5;border-radius:0 4px 4px 0;font-family:{FONT};">'
        f'<div style="font-size:11px;font-weight:600;color:{COLOR_MUTED};margin-bottom:4px;">说明</div>'
        f'<ul style="margin:0;padding-left:18px;font-size:11px;color:{COLOR_MUTED};">{lis}</ul></div>'
    )


def _muted_line(text: str) -> str:
    return f'<div style="margin-top:10px;font-size:11px;line-height:1.7;color:{COLOR_MUTED};">{esc(text)}</div>'


# ========== 组合日报 ==========

def _sort_by(items: List[dict], key: str) -> List[dict]:
    """按金额降序，缺失值的沉底（与页面的 sortItemsByPnl 同一口径）。"""
    def sort_key(item: dict):
        value = _num(item.get(key))
        return (1, 0.0) if value is None else (0, -value)

    return sorted(items, key=sort_key)


def _render_portfolio(payload: dict) -> str:
    summary = payload.get("summary") or {}
    portfolio = payload.get("portfolio") or {}
    name = portfolio.get("name") or "投资组合"
    items = payload.get("items") or []

    asset_count = int(_num(summary.get("asset_count")) or 0)
    covered = int(_num(summary.get("day_pnl_covered_count")) or 0)
    day_pnl = summary.get("day_pnl")
    day_pct = summary.get("day_pnl_percent")
    holding_pnl = summary.get("holding_pnl")

    parts: List[str] = [
        _section_title(
            f"{name}",
            f"组合日报 · {asset_count} 只标的 · 今日盈亏口径为「现价 − 昨收」",
        )
    ]

    # 空组合不画 6 张 KPI 卡：那会得到一整屏「— / ¥0.00」，把真正有内容的
    # 组合挤到屏幕外。一句话说明 + notes 足够。
    if not items:
        notes = payload.get("notes") or []
        if notes:
            parts.append(_notes_block(notes))
        else:
            parts.append(_muted_line("该组合还没有资产，日报只包含空快照。"))
        return "".join(parts)

    parts.append(
        _kpi_grid(
            [
                (
                    "今日盈亏",
                    signed_money(day_pnl),
                    f"{covered}/{asset_count} 只有昨收" if asset_count else "无标的",
                    tone_color(day_pnl),
                ),
                (
                    "今日涨跌幅",
                    percent(day_pct),
                    "按昨日市值加权",
                    tone_color(day_pct),
                ),
                (
                    "持仓市值",
                    money(summary.get("market_value")),
                    "按今日收盘价",
                    COLOR_TEXT,
                ),
                (
                    "持仓盈亏",
                    signed_money(holding_pnl),
                    f"累计 {percent(summary.get('holding_pnl_percent'))}"
                    if holding_pnl is not None
                    else "未填成本，无法统计",
                    tone_color(holding_pnl),
                ),
                (
                    "年化股息收入",
                    money(summary.get("annual_income")),
                    "持股数 × TTM 每股分红",
                    COLOR_TEXT,
                ),
                (
                    "加权股息率",
                    percent(summary.get("weighted_ttm_yield"), signed=False),
                    "组合整体口径",
                    COLOR_TEXT,
                ),
            ]
        )
    )

    head = [
        _th("标的", width="30%"),
        _th("现价", align="right", width="11%"),
        _th("今日涨跌", align="right", width="17%"),
        _th("今日盈亏", align="right", width="14%"),
        _th("市值", align="right", width="13%"),
        _th("持仓盈亏", align="right", width="15%"),
    ]

    body: List[str] = []
    for item in _sort_by(items, "day_pnl")[:MAX_ITEMS_IN_MAIL]:
        pct = item.get("change_percent")
        change = item.get("change")
        change_html = (
            f'<span style="color:{tone_color(pct)};font-weight:600;">{esc(percent(pct))}</span>'
            f'<br><span style="font-size:10px;color:{COLOR_MUTED};">'
            f"{esc(price(change, item.get('asset_type')))}</span>"
            if pct is not None
            else f'<span style="color:{COLOR_MUTED};">{DASH}</span>'
        )
        body.append(
            _row(
                [
                    _td(
                        f'<span style="color:{COLOR_TEXT};">{esc(item.get("name"))}</span>'
                        f'<br><span style="font-size:10px;color:{COLOR_MUTED};">'
                        f'{esc(item.get("code"))} · {esc(item.get("asset_type"))}</span>'
                    ),
                    _td(esc(price(item.get("price"), item.get("asset_type"))), align="right"),
                    _td(change_html, align="right"),
                    _td(
                        f'<span style="color:{tone_color(item.get("day_pnl"))};font-weight:600;">'
                        f'{esc(signed_money(item.get("day_pnl")))}</span>',
                        align="right",
                    ),
                    _td(esc(money(item.get("market_value"))), align="right"),
                    _td(
                        f'<span style="color:{tone_color(item.get("holding_pnl"))};">'
                        f'{esc(signed_money(item.get("holding_pnl")))}</span>',
                        align="right",
                    ),
                ]
            )
        )

    # 合计行：金额全部由明细相加而来（后端 summarize_holdings 就是这么算的）
    body.append(
        _row(
            [
                _td("合计", bold=True, bg="#f7f9fc"),
                _td("", align="right", bg="#f7f9fc"),
                _td("", align="right", bg="#f7f9fc"),
                _td(
                    f'<span style="color:{tone_color(day_pnl)};font-weight:600;">'
                    f'{esc(signed_money(day_pnl))}</span>',
                    align="right",
                    bg="#f7f9fc",
                ),
                _td(
                    f'<b>{esc(money(summary.get("market_value")))}</b>',
                    align="right",
                    bg="#f7f9fc",
                ),
                _td(
                    f'<span style="color:{tone_color(holding_pnl)};font-weight:600;">'
                    f'{esc(signed_money(holding_pnl))}</span>',
                    align="right",
                    bg="#f7f9fc",
                ),
            ]
        )
    )

    parts.append(f'<div style="margin-top:10px;">{_table(head, body)}</div>')
    if len(items) > MAX_ITEMS_IN_MAIL:
        parts.append(
            _muted_line(
                f"共 {len(items)} 只标的，邮件只列出今日盈亏最靠前的 "
                f"{MAX_ITEMS_IN_MAIL} 只（合计行仍为全部标的之和）"
            )
        )

    # 涨跌榜
    gainers = payload.get("top_gainers") or []
    losers = payload.get("top_losers") or []
    if gainers or losers:
        parts.append(_movers_line(gainers, losers))

    # 收入结构 + 集中度
    income = payload.get("income_top") or []
    concentration = payload.get("concentration") or {}
    if income or concentration.get("top1_share_percent") is not None:
        parts.append(_income_block(income, concentration, summary))

    # 目标进度
    goals = payload.get("goals") or []
    if goals:
        parts.append(_goals_block(goals))

    parts.append(_notes_block(payload.get("notes") or []))
    return "".join(parts)


def _movers_line(gainers: List[dict], losers: List[dict]) -> str:
    def fmt(rows: List[dict]) -> str:
        return "、".join(
            f'{esc(r.get("name"))} <span style="color:{tone_color(r.get("day_pnl"))};">'
            f'{esc(signed_money(r.get("day_pnl")))}</span>'
            for r in rows
        )

    pieces = []
    if gainers:
        pieces.append(f"<b>赚得最多</b>　{fmt(gainers)}")
    if losers:
        pieces.append(f"<b>亏得最多</b>　{fmt(losers)}")
    return (
        f'<div style="margin-top:14px;padding:10px 14px;background-color:#fbfcfe;'
        f'border:1px solid {COLOR_BORDER};border-radius:6px;font-size:12px;line-height:2;'
        f'color:{COLOR_TEXT};font-family:{FONT};">'
        + "<br>".join(pieces)
        + "</div>"
    )


def _income_block(income: List[dict], concentration: dict, summary: dict) -> str:
    rows_html = ""
    if income:
        max_share = max((_num(r.get("share_percent")) or 0.0) for r in income) or 1.0
        lines = []
        for row in income:
            share = _num(row.get("share_percent")) or 0.0
            # 相对最大项画满格，否则 21% 的条形短到看不见（与页面同一处理）
            lines.append(
                f'<tr><td style="padding:5px 0;font-size:12px;color:{COLOR_TEXT};'
                f'font-family:{FONT};">{esc(row.get("name"))}</td>'
                f'<td width="42%" style="padding:5px 10px;">{_bar(share / max_share * 100)}</td>'
                f'<td width="18%" align="right" style="padding:5px 0;font-size:12px;'
                f'color:{COLOR_TEXT};font-family:{FONT};font-variant-numeric:tabular-nums;">'
                f"{esc(percent(share, signed=False))}</td></tr>"
            )
        rows_html = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            + "".join(lines)
            + "</table>"
        )

    conc = ""
    if concentration.get("top1_share_percent") is not None:
        conc = (
            f'<div style="margin-top:10px;font-size:11px;color:{COLOR_MUTED};line-height:1.8;">'
            f'最大持仓 {esc(concentration.get("top1_name"))} 占 '
            f'{esc(percent(concentration.get("top1_share_percent"), signed=False))}'
            f'（市值口径）；前三大合计 '
            f'{esc(percent(concentration.get("top3_share_percent"), signed=False))}</div>'
        )

    if not rows_html and not conc:
        return ""

    label = "年化股息贡献" if income else ""
    return (
        f'<div style="margin-top:16px;font-size:12px;color:{COLOR_TEXT};font-family:{FONT};">'
        f'<div style="font-size:11px;font-weight:600;color:{COLOR_MUTED};margin-bottom:4px;">'
        f"{label}</div>{rows_html}{conc}</div>"
    )


def _goals_block(goals: List[dict]) -> str:
    rows = []
    for goal in goals:
        current = _num(goal.get("current_annual_income")) or 0.0
        target = _num(goal.get("target_annual_income")) or 0.0
        progress = _num(goal.get("progress_percent")) or 0.0
        achieved = bool(goal.get("is_achieved"))
        gap_text = (
            "已达成 🎉"
            if achieved
            else f'还差 {esc(money(goal.get("gap_annual")))}/年'
        )
        rows.append(
            f'<tr><td style="padding:6px 0;font-size:12px;color:{COLOR_TEXT};font-family:{FONT};">'
            f'{esc(goal.get("name"))}'
            f'<div style="font-size:10px;color:{COLOR_MUTED};">'
            f"目标 {esc(money(target))}/年</div></td>"
            f'<td width="40%" style="padding:6px 12px;">{_bar(progress, COLOR_UP if achieved else COLOR_BRAND)}</td>'
            f'<td width="22%" align="right" style="padding:6px 0;font-size:12px;'
            f'color:{COLOR_UP if achieved else COLOR_TEXT};font-family:{FONT};'
            f'font-variant-numeric:tabular-nums;">'
            f"{esc(percent(progress, signed=False))}"
            f'<div style="font-size:10px;color:{COLOR_MUTED};">{gap_text}</div></td></tr>'
        )
    return (
        f'<div style="margin-top:16px;font-family:{FONT};">'
        f'<div style="font-size:11px;font-weight:600;color:{COLOR_MUTED};margin-bottom:4px;">'
        f"年收入目标进度</div>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        + "".join(rows)
        + "</table></div>"
    )


# ========== 观察池日报 ==========

YIELD_SIGNAL_LABELS = {
    "hit": "股息达标",
    "near": "股息接近",
    "watch": "观察",
    "unknown": "无数据",
}
PRICE_SIGNAL_LABELS = {
    "hit": "价格达标",
    "near": "价格接近",
    "watch": "等待",
    "not_set": "未设目标价",
    "unknown": "无数据",
}


def _watchlist_signal_text(item: dict) -> Tuple[str, str]:
    """把两条信号合成一句话 + 颜色（达标红、接近橙、其余灰）。"""
    pieces = []
    strongest = 0
    for key, labels in (
        ("yield_signal", YIELD_SIGNAL_LABELS),
        ("price_signal", PRICE_SIGNAL_LABELS),
    ):
        signal = item.get(key)
        if signal in ("hit", "near"):
            pieces.append(labels[signal])
            strongest = max(strongest, 2 if signal == "hit" else 1)
    if not pieces:
        return DASH, COLOR_MUTED
    color = COLOR_UP if strongest == 2 else "#c26a00"
    return "、".join(pieces), color


def _render_watchlist(payload: dict) -> str:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    notional = _num(summary.get("notional_per_item")) or 0.0
    day_pnl = summary.get("day_pnl")
    today_count = int(_num(summary.get("today_count")) or 0)
    item_count = int(_num(summary.get("item_count")) or 0)

    parts: List[str] = [
        _section_title(
            "观察池",
            f"观察池日报 · {item_count} 只标的 · 今日盈亏按每只 {money(notional)} 等权测算",
        )
    ]

    parts.append(
        _kpi_grid(
            [
                (
                    "今日测算盈亏",
                    signed_money(day_pnl),
                    f"每只按 {money(notional)} 测算",
                    tone_color(day_pnl),
                ),
                ("平均涨跌幅", percent(summary.get("avg_change_percent")), "等权算术平均", tone_color(summary.get("avg_change_percent"))),
                (
                    "计入今日",
                    f"{today_count} / {item_count}",
                    "只有今天有快照的才计入",
                    COLOR_TEXT,
                ),
                (
                    "股息率达标",
                    f"{int(_num(summary.get('yield_hit_count')) or 0)} 只",
                    "当前股息率 ≥ 目标",
                    COLOR_UP if (int(_num(summary.get("yield_hit_count")) or 0) > 0) else COLOR_TEXT,
                ),
                (
                    "目标价达标",
                    f"{int(_num(summary.get('price_hit_count')) or 0)} 只",
                    "现价 ≤ 目标价",
                    COLOR_UP if (int(_num(summary.get("price_hit_count")) or 0) > 0) else COLOR_TEXT,
                ),
                (
                    "接近达标",
                    f"{int(_num(summary.get('yield_near_count')) or 0) + int(_num(summary.get('price_near_count')) or 0)} 只",
                    "差 3% 以内",
                    "#c26a00",
                ),
            ]
        )
    )

    if not items:
        parts.append(_muted_line("观察池为空。"))
        parts.append(_notes_block(payload.get("notes") or []))
        return "".join(parts)

    head = [
        _th("标的", width="32%"),
        _th("现价", align="right", width="12%"),
        _th("今日涨跌", align="right", width="16%"),
        _th("测算盈亏", align="right", width="14%"),
        _th("当前股息率", align="right", width="13%"),
        _th("目标股息率", align="right", width="13%"),
    ]

    body: List[str] = []
    today_items = [i for i in items if i.get("is_today")]
    for item in _sort_by(today_items or items, "notional_pnl")[:MAX_ITEMS_IN_MAIL]:
        signal_text, signal_color = _watchlist_signal_text(item)
        body.append(
            _row(
                [
                    _td(
                        f'<span style="color:{COLOR_TEXT};">{esc(item.get("name"))}</span>'
                        f'<br><span style="font-size:10px;color:{COLOR_MUTED};">'
                        f'{esc(item.get("code"))} · {esc(item.get("asset_type"))}</span>'
                    ),
                    _td(esc(price(item.get("price"), item.get("asset_type"))), align="right"),
                    _td(
                        f'<span style="color:{tone_color(item.get("change_percent"))};font-weight:600;">'
                        f'{esc(percent(item.get("change_percent")))}</span>'
                        f'<br><span style="font-size:10px;color:{COLOR_MUTED};">'
                        f'{esc(price(item.get("change"), item.get("asset_type")))}</span>',
                        align="right",
                    ),
                    _td(
                        f'<span style="color:{tone_color(item.get("notional_pnl"))};">'
                        f'{esc(signed_money(item.get("notional_pnl")))}</span>',
                        align="right",
                    ),
                    _td(esc(percent(item.get("current_yield"), signed=False)), align="right"),
                    _td(esc(percent(item.get("target_yield"), signed=False)), align="right"),
                ]
            )
        )

    # 信号单独一张小表：塞进主表会把它挤成 7 列，窄屏必挤爆
    signal_rows = []
    for item in items:
        signal_text, signal_color = _watchlist_signal_text(item)
        if signal_text == DASH:
            continue
        signal_rows.append(
            f'<span style="display:inline-block;margin:0 8px 6px 0;padding:3px 9px;'
            f'border:1px solid {signal_color};border-radius:12px;font-size:11px;'
            f'color:{signal_color};font-family:{FONT};">'
            f'{esc(item.get("name"))} · {esc(signal_text)}</span>'
        )

    parts.append(f'<div style="margin-top:10px;">{_table(head, body)}</div>')

    parts.append(_watchlist_hits_block(payload))
    if signal_rows:
        parts.append(
            f'<div style="margin-top:14px;font-size:11px;color:{COLOR_MUTED};'
            f'font-family:{FONT};">信号</div>'
            f'<div style="margin-top:6px;">{"".join(signal_rows)}</div>'
        )

    parts.append(_notes_block(payload.get("notes") or []))
    return "".join(parts)


def _watchlist_hits_block(payload: dict) -> str:
    """「有没有股息率 / 目标价满足要求的标的」—— 用户看日报最关心的两句话。"""

    blocks = []

    yield_hits = payload.get("yield_hits") or []
    if yield_hits:
        head = [
            _th("标的", width="40%"),
            _th("当前股息率", align="right"),
            _th("目标股息率", align="right"),
            _th("超出", align="right"),
        ]
        body = []
        for item in yield_hits:
            gap = _num(item.get("yield_gap_points"))
            body.append(
                _row(
                    [
                        _td(esc(item.get("name"))),
                        _td(esc(percent(item.get("current_yield"), signed=False)), align="right", color=COLOR_UP),
                        _td(esc(percent(item.get("target_yield"), signed=False)), align="right"),
                        _td(
                            f"+{percent(gap, signed=False)}" if gap is not None else DASH,
                            align="right",
                            color=COLOR_UP,
                        ),
                    ]
                )
            )
        blocks.append(
            f'<div style="margin-top:16px;font-family:{FONT};">'
            f'<div style="font-size:12px;font-weight:600;color:{COLOR_UP};margin-bottom:6px;">'
            f"✅ 股息率达标 {len(yield_hits)} 只</div>"
            + _table(head, body)
            + "</div>"
        )

    price_hits = payload.get("price_hits") or []
    if price_hits:
        head = [
            _th("标的", width="40%"),
            _th("现价", align="right"),
            _th("目标价", align="right"),
            _th("距目标", align="right"),
        ]
        body = []
        for item in price_hits:
            body.append(
                _row(
                    [
                        _td(esc(item.get("name"))),
                        _td(esc(price(item.get("price"), item.get("asset_type"))), align="right"),
                        _td(esc(price(item.get("target_price"), item.get("asset_type"))), align="right"),
                        _td(
                            esc(percent(item.get("price_gap_percent"))),
                            align="right",
                            color=COLOR_MUTED,
                        ),
                    ]
                )
            )
        blocks.append(
            f'<div style="margin-top:16px;font-family:{FONT};">'
            f'<div style="font-size:12px;font-weight:600;color:{COLOR_UP};margin-bottom:6px;">'
            f"✅ 目标价达标 {len(price_hits)} 只</div>"
            + _table(head, body)
            + "</div>"
        )

    if not blocks:
        blocks.append(
            f'<div style="margin-top:16px;padding:10px 14px;background-color:#fbfcfe;'
            f'border:1px dashed {COLOR_BORDER};border-radius:6px;font-size:12px;'
            f'color:{COLOR_MUTED};font-family:{FONT};">'
            f"今天没有股息率或目标价达标的标的。</div>"
        )

    return "".join(blocks)


# ========== 整封邮件 ==========

def _combined_pnl(payloads: Sequence[dict], report_type: str) -> Tuple[Optional[float], int]:
    """某类日报的今日盈亏合计。返回 (金额, 参与份数)；一份都算不出 → (None, 0)。"""
    total = 0.0
    counted = 0
    for payload in payloads:
        if payload.get("type") != report_type:
            continue
        value = _num((payload.get("summary") or {}).get("day_pnl"))
        if value is None:
            continue
        total += value
        counted += 1
    return (round(total, 2), counted) if counted else (None, 0)


def build_subject(payloads: Sequence[dict], report_date: date) -> str:
    """主题：日期 + 两边盈亏，一眼就能在收件箱里看出今天怎么样。"""
    prefix = (settings.mail_subject_prefix or "").strip() or "投资日报"
    pieces = []
    portfolio_pnl, portfolio_count = _combined_pnl(payloads, "portfolio")
    if portfolio_count:
        label = "组合" if portfolio_count == 1 else f"组合×{portfolio_count}"
        pieces.append(f"{label} {signed_money(portfolio_pnl)}")
    watchlist_pnl, watchlist_count = _combined_pnl(payloads, "watchlist")
    if watchlist_count:
        pieces.append(f"观察池 {signed_money(watchlist_pnl)}")

    head = f"{report_date.isoformat()} {prefix}"
    return " · ".join([head] + pieces)


def _preheader(payloads: Sequence[dict]) -> str:
    pieces = []
    for report_type, label in (("portfolio", "组合"), ("watchlist", "观察池")):
        pnl, count = _combined_pnl(payloads, report_type)
        if count:
            pieces.append(f"{label} {signed_money(pnl)}")
    return "今日 " + "，".join(pieces) if pieces else "今日日报"


def render_daily_email(
    payloads: Sequence[dict],
    report_date: date,
    generated_at: Optional[datetime] = None,
) -> Tuple[str, str]:
    """渲染整封邮件，返回 (纯文本, HTML)。

    纯文本版不是「顺手加的」：有些客户端默认不显示 HTML，另外同时给两版
    对垃圾邮件评分也更友好。
    """
    stamp = (generated_at or datetime.now(SHANGHAI)).strftime("%Y-%m-%d %H:%M")
    portfolios = sorted(
        (p for p in payloads if p.get("type") == "portfolio"),
        key=lambda p: (p.get("portfolio") or {}).get("id") or 0,
    )
    watchlists = [p for p in payloads if p.get("type") == "watchlist"]

    sections: List[str] = []
    for payload in portfolios:
        sections.append(_render_portfolio(payload))
    for payload in watchlists:
        sections.append(_render_watchlist(payload))

    if not sections:
        sections.append(_muted_line("今天没有任何日报内容。"))

    hero_pnl, hero_count = _combined_pnl(payloads, "portfolio")
    if not hero_count:
        hero_pnl, hero_count = _combined_pnl(payloads, "watchlist")
    hero_color = tone_color(hero_pnl)

    # 涨跌幅只在「只有一份日报」时展示：多个组合的涨跌幅不能相加，
    # 硬把第一个组合的百分比放在总额旁边，读者会以为那是整体涨跌幅。
    hero_pct = None
    single = portfolios if len(portfolios) == 1 else (watchlists if not portfolios and len(watchlists) == 1 else [])
    if single:
        hero_pct = (single[0].get("summary") or {}).get("day_pnl_percent")

    hero_pct_html = (
        f'<span style="font-size:15px;font-weight:500;color:{hero_color};">'
        f"{esc(percent(hero_pct))}</span>"
        if hero_pct is not None
        else ""
    )
    if len(portfolios) > 1:
        hero_label = "今日盈亏 · 组合合计"
    elif portfolios:
        hero_label = "今日盈亏"
    else:
        hero_label = "观察池测算盈亏（等权）"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(build_subject(payloads, report_date))}</title></head>
<body style="margin:0;padding:0;background-color:#f2f4f7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{esc(_preheader(payloads))}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="background-color:#f2f4f7;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="max-width:720px;background-color:#ffffff;border:1px solid {COLOR_BORDER};
      border-radius:10px;font-family:{FONT};">
      <tr><td style="padding:24px 28px 8px;">
        <div style="font-size:11px;letter-spacing:1px;color:{COLOR_MUTED};text-transform:uppercase;">
          {report_date.strftime("%Y 年 %m 月 %d 日")} · 每日投资日报</div>
        <div style="margin-top:10px;font-size:30px;font-weight:700;line-height:1.3;
          color:{hero_color};font-variant-numeric:tabular-nums;">
          {esc(signed_money(hero_pnl))}
          {hero_pct_html}</div>
        <div style="margin-top:4px;font-size:12px;color:{COLOR_MUTED};">
          {esc(hero_label)} · 生成于 {esc(stamp)}</div>
      </td></tr>
      <tr><td style="padding:0 28px 24px;">{''.join(sections)}</td></tr>
      <tr><td style="padding:16px 28px 24px;border-top:1px solid {COLOR_BORDER};">
        <div style="font-size:11px;line-height:1.8;color:{COLOR_MUTED};">
          今日盈亏 = Σ(现价 − 昨收) × 持股数，与「持仓盈亏」（相对成本）口径不同；<br>
          观察池没有持股数，盈亏按每只固定本金等权测算，仅作参考。<br>
          完整明细与历史日报请在本机 portfolio 应用中查看。
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    return _render_text(payloads, report_date, stamp), html


def _render_text(payloads: Sequence[dict], report_date: date, stamp: str) -> str:
    """纯文本版：不追求对齐（中英文混排宽度不一致），只保证信息完整可读。"""
    lines: List[str] = [f"{report_date.isoformat()} 每日投资日报", f"生成于 {stamp}", ""]

    for payload in sorted(
        (p for p in payloads if p.get("type") == "portfolio"),
        key=lambda p: (p.get("portfolio") or {}).get("id") or 0,
    ):
        summary = payload.get("summary") or {}
        name = (payload.get("portfolio") or {}).get("name") or "投资组合"
        items = _sort_by(payload.get("items") or [], "day_pnl")
        lines.append(f"■ {name}")
        if not items:
            lines.append("  （该组合还没有资产）")
            lines.append("")
            continue
        lines.append(
            f"  今日盈亏 {signed_money(summary.get('day_pnl'))}"
            f"（{percent(summary.get('day_pnl_percent'))}）"
        )
        lines.append(
            f"  持仓市值 {money(summary.get('market_value'))}"
            f"｜持仓盈亏 {signed_money(summary.get('holding_pnl'))}"
            f"（{percent(summary.get('holding_pnl_percent'))}）"
        )
        lines.append(
            f"  年化股息收入 {money(summary.get('annual_income'))}"
            f"｜加权股息率 {percent(summary.get('weighted_ttm_yield'), signed=False)}"
        )
        lines.append("  明细：")
        for item in items[:MAX_ITEMS_IN_MAIL]:
            lines.append(
                f"    {item.get('name')}({item.get('code')})"
                f" 现价 {price(item.get('price'), item.get('asset_type'))}"
                f" 今日 {percent(item.get('change_percent'))}"
                f" 盈亏 {signed_money(item.get('day_pnl'))}"
                f" 市值 {money(item.get('market_value'))}"
                f" 持仓 {signed_money(item.get('holding_pnl'))}"
            )
        for goal in payload.get("goals") or []:
            lines.append(
                f"  目标 {goal.get('name')}："
                f"{percent(goal.get('progress_percent'), signed=False)}"
                + (
                    "（已达成）"
                    if goal.get("is_achieved")
                    else f"（还差 {money(goal.get('gap_annual'))}/年）"
                )
            )
        for note in payload.get("notes") or []:
            lines.append(f"  说明：{note}")
        lines.append("")

    for payload in (p for p in payloads if p.get("type") == "watchlist"):
        summary = payload.get("summary") or {}
        lines.append("■ 观察池")
        lines.append(
            f"  今日测算盈亏 {signed_money(summary.get('day_pnl'))}"
            f"（{percent(summary.get('day_pnl_percent'))}，"
            f"每只按 {money(summary.get('notional_per_item'))} 测算）"
        )
        lines.append(
            f"  上涨 {int(_num(summary.get('up_count')) or 0)} 只"
            f"｜下跌 {int(_num(summary.get('down_count')) or 0)} 只"
            f"｜股息率达标 {int(_num(summary.get('yield_hit_count')) or 0)} 只"
            f"｜目标价达标 {int(_num(summary.get('price_hit_count')) or 0)} 只"
        )
        today_items = [i for i in (payload.get("items") or []) if i.get("is_today")]
        if today_items:
            lines.append("  明细：")
            for item in _sort_by(today_items, "notional_pnl"):
                signal_text, _ = _watchlist_signal_text(item)
                lines.append(
                    f"    {item.get('name')}({item.get('code')})"
                    f" 现价 {price(item.get('price'), item.get('asset_type'))}"
                    f" 今日 {percent(item.get('change_percent'))}"
                    f" 测算 {signed_money(item.get('notional_pnl'))}"
                    f" 股息率 {percent(item.get('current_yield'), signed=False)}"
                    f"/{percent(item.get('target_yield'), signed=False)}"
                    f" {signal_text}"
                )
        hits = payload.get("yield_hits") or []
        if hits:
            lines.append(
                "  股息率达标：" + "、".join(str(i.get("name")) for i in hits)
            )
        price_hits = payload.get("price_hits") or []
        if price_hits:
            lines.append(
                "  目标价达标：" + "、".join(str(i.get("name")) for i in price_hits)
            )
        for note in payload.get("notes") or []:
            lines.append(f"  说明：{note}")
        lines.append("")

    lines.append("--")
    lines.append("由 portfolio 每日定时任务自动发送。")
    return "\n".join(lines)


# ========== 发送 ==========

@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    timeout: int

    @classmethod
    def from_settings(cls) -> "SmtpConfig":
        return cls(
            host=(settings.mail_smtp_host or "").strip(),
            port=int(settings.mail_smtp_port or 465),
            use_ssl=bool(settings.mail_smtp_ssl),
            username=(settings.mail_username or "").strip(),
            password=settings.mail_password or "",
            timeout=int(settings.mail_timeout_seconds or 20),
        )


def build_message(
    subject: str,
    text: str,
    html: str,
    recipients: Sequence[str],
    *,
    sender_name: Optional[str] = None,
    sender_addr: Optional[str] = None,
    config: Optional[SmtpConfig] = None,
) -> EmailMessage:
    """组装 MIME 消息（text + html 双版本）。

    `From` 必须与 SMTP 登录账号一致：QQ / 163 等会校验，不一致直接拒信
    （报「mail from address must be same as authorization user」）。
    """
    cfg = config or SmtpConfig.from_settings()
    address = (sender_addr or cfg.username or "").strip()
    name = (sender_name if sender_name is not None else settings.mail_from_name) or ""
    domain = address.split("@")[-1] if "@" in address else None

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((name, address)) if name else address
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def smtp_send(message: EmailMessage, config: SmtpConfig) -> None:
    """真正的网络 I/O：同步实现，调用方用 `asyncio.to_thread` 包起来。

    错误统一翻译成 `MailSendError`，并把排查方向写进文案：
    - 535 `Authentication failed` → 用了登录密码而不是授权码；
    - **登录阶段直接断连** → QQ 邮箱的另一种「认证失败」表现（实测：授权码错误时
      服务器在 AUTH 之后直接切断连接，连 535 都不给），不能让它以
      「Connection unexpectedly closed」的原始形态抛给用户。
    """
    try:
        if config.use_ssl:
            client = smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout)
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
            client.ehlo()
            client.starttls()
            client.ehlo()
    except (smtplib.SMTPException, OSError) as exc:
        raise MailSendError(
            f"连不上 {config.host}:{config.port}（{type(exc).__name__}: {exc}）"
        ) from exc

    try:
        try:
            client.login(config.username, config.password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailSendError(
                f"SMTP 认证失败（{exc.smtp_code}）：QQ / 163 邮箱要用「授权码」而不是登录密码，"
                "在邮箱设置里开启 SMTP 服务后生成"
            ) from exc
        except smtplib.SMTPServerDisconnected as exc:
            raise MailSendError(
                "登录过程中服务器直接断开连接：多半仍是账号或授权码不对"
                "（QQ 邮箱会用断连代替 535），也可能是同一 IP 失败次数过多被临时限制，"
                "稍后重试或重新生成授权码"
            ) from exc

        try:
            client.send_message(message)
        except smtplib.SMTPException as exc:
            raise MailSendError(f"发送被拒：{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            client.quit()
        except Exception:                       # 已经出错时 quit 再抛会盖掉真正原因
            pass


def _ensure_configured(recipients: Sequence[str]) -> None:
    config = SmtpConfig.from_settings()
    if not config.username or not config.password:
        missing = "、".join(settings.mail_missing_config) or "MAIL_USERNAME / MAIL_PASSWORD"
        raise MailNotConfigured(f"邮件未配置：请在 .env 里填写 {missing}")
    if not recipients:
        raise MailNotConfigured("邮件未配置：没有收件人（MAIL_RECIPIENTS）")


def load_payloads_for_date(db: Session, report_date: date) -> List[dict]:
    """取某一天的全部日报 payload：组合按 id 在前，观察池在后。

    坏 JSON 的报告直接跳过（`report_payload` 已兜底成 `{}`）——
    一封缺了一节的邮件，好过整封发不出去。
    """
    rows = (
        db.query(DailyReport)
        .filter(DailyReport.report_date == report_date)
        .order_by(DailyReport.report_type.asc(), DailyReport.scope_id.asc())
        .all()
    )
    payloads: List[dict] = []
    for row in rows:
        payload = report_payload(row)
        if payload:
            payloads.append(payload)
    return payloads


async def send_daily_reports(
    db: Session,
    report_date: Optional[date] = None,
    recipients: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """把某一天的日报合成一封邮件发出。返回发送摘要（给接口 / 任务记录用）。"""
    target_date = report_date or datetime.now(SHANGHAI).date()
    targets = list(recipients or settings.mail_recipient_list)
    _ensure_configured(targets)

    payloads = load_payloads_for_date(db, target_date)
    if not payloads:
        raise NoReportsError(f"{target_date.isoformat()} 还没有生成日报，未发送邮件")

    subject = build_subject(payloads, target_date)
    text, html = render_daily_email(payloads, target_date)
    message = build_message(subject, text, html, targets)

    try:
        await asyncio.to_thread(smtp_send, message, SmtpConfig.from_settings())
    except MailSendError:
        logger.warning("daily report mail: send failed date=%s", target_date, exc_info=True)
        raise

    logger.info(
        "daily report mail: sent date=%s reports=%d to=%s",
        target_date, len(payloads), ", ".join(targets),
    )
    return {
        "report_date": target_date,
        "report_count": len(payloads),
        "recipients": targets,
        "subject": subject,
    }


async def send_test_mail(recipients: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """发一封测试邮件：只验证 SMTP 通不通，不带任何持仓数据。"""
    targets = list(recipients or settings.mail_recipient_list)
    _ensure_configured(targets)

    now = datetime.now(SHANGHAI)
    subject = f"[测试] {settings.mail_subject_prefix or '投资日报'}邮件通道正常"
    text = (
        f"这是一封测试邮件，发送于 {now.strftime('%Y-%m-%d %H:%M:%S')}（Asia/Shanghai）。\n\n"
        "收到它说明 SMTP 配置可用，之后每天 15:50 会自动发送当日日报。\n"
    )
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background-color:#f2f4f7;font-family:{FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
  style="max-width:560px;background-color:#ffffff;border:1px solid {COLOR_BORDER};
  border-radius:10px;padding:24px 28px;">
<tr><td>
<div style="font-size:15px;font-weight:600;color:{COLOR_TEXT};">邮件通道正常 ✅</div>
<div style="margin-top:10px;font-size:12px;line-height:1.9;color:{COLOR_MUTED};">
这封测试邮件发送于 {esc(now.strftime("%Y-%m-%d %H:%M:%S"))}（Asia/Shanghai）。<br>
之后每天 15:50（日报生成完成后）会自动把当日日报发到这个地址。<br>
如果不想再收，把 <code>.env</code> 里的 <code>DAILY_REPORT_MAIL_ENABLED</code> 设为 false 并重启后端。
</div></td></tr></table>
</td></tr></table>
</body></html>"""

    message = build_message(subject, text, html, targets)
    await asyncio.to_thread(smtp_send, message, SmtpConfig.from_settings())
    return {"recipients": targets, "subject": subject}


__all__ = [
    "MailNotConfigured",
    "MailSendError",
    "NoReportsError",
    "SmtpConfig",
    "build_message",
    "build_subject",
    "load_payloads_for_date",
    "render_daily_email",
    "send_daily_reports",
    "send_test_mail",
    "smtp_send",
]
