"""日报邮件：渲染口径、MIME 结构、发送链路、调度任务。

这几层刻意分开测：
- **渲染**（纯函数）：断言 HTML 里确实有那些数字，且「不知道」显示「—」而不是 ¥0.00；
- **MIME**：收件人 / From / 双版本正文 —— 配错这三个，邮件要么进垃圾箱要么被拒；
- **发送**：`smtp_send` 换成假的，验证「哪天的日报被发了、发给谁」；
- **任务**：未配置 → skipped、当天没日报 → failed、正常 → success。
"""
import asyncio
import json
from datetime import date, datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest

import report_mail
from config import settings
from database import SessionLocal
from models import DailyReport
from report_mail import (
    MailNotConfigured,
    MailSendError,
    NoReportsError,
    build_message,
    build_subject,
    render_daily_email,
    send_daily_reports,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------- 夹具 ----------

def today() -> date:
    return datetime.now(SHANGHAI).date()


def portfolio_payload(**overrides) -> dict:
    """一份「有数据」的组合日报 payload（字段与 daily_reports 落的库一致）。"""
    payload = {
        "type": "portfolio",
        "date": "2026-11-07",
        "generated_at": "2026-11-07 15:40:00",
        "portfolio": {"id": 1, "name": "稳健组合"},
        "summary": {
            "market_value": 100000.0,
            "prev_market_value": 99000.0,
            "day_pnl": 1000.0,
            "day_pnl_percent": 1.01,
            "cost_amount": 80000.0,
            "holding_pnl": 20000.0,
            "holding_pnl_percent": 25.0,
            "annual_income": 4000.0,
            "weighted_ttm_yield": 4.0,
            "asset_count": 2,
            "day_pnl_covered_count": 2,
            "cost_covered_count": 2,
            "missing_prev_close_count": 0,
        },
        "items": [
            {
                "id": 1,
                "code": "600036",
                "name": "招商银行",
                "asset_type": "股票",
                "quantity": 100,
                "price": 40.0,
                "prev_close": 39.0,
                "change": 1.0,
                "change_percent": 2.56,
                "day_pnl": 100.0,
                "market_value": 4000.0,
                "holding_pnl": 500.0,
                "holding_pnl_percent": 14.28,
                "dividend_per_share": 2.0,
                "ttm_yield": 5.0,
                "annual_income": 200.0,
                "price_source": "real",
            },
            {
                "id": 2,
                "code": "510170",
                "name": "红利国企ETF国泰",
                "asset_type": "ETF",
                "quantity": 10000,
                "price": 1.2,
                "prev_close": 1.11,
                "change": 0.09,
                "change_percent": 8.11,
                "day_pnl": 900.0,
                "market_value": 12000.0,
                "holding_pnl": None,
                "holding_pnl_percent": None,
                "dividend_per_share": 0.05,
                "ttm_yield": 4.17,
                "annual_income": 500.0,
                "price_source": "real",
            },
        ],
        "top_gainers": [
            {"name": "红利国企ETF国泰", "day_pnl": 900.0, "change_percent": 8.11}
        ],
        "top_losers": [],
        "income_top": [
            {"code": "510170", "name": "红利国企ETF国泰", "annual_income": 500.0, "share_percent": 71.4},
            {"code": "600036", "name": "招商银行", "annual_income": 200.0, "share_percent": 28.6},
        ],
        "concentration": {
            "top1_code": "510170",
            "top1_name": "红利国企ETF国泰",
            "top1_share_percent": 12.0,
            "top3_share_percent": 16.0,
        },
        "goals": [
            {
                "id": 1,
                "name": "稳健目标",
                "target_annual_income": 5000.0,
                "current_annual_income": 4000.0,
                "gap_annual": 1000.0,
                "progress_percent": 80.0,
                "is_achieved": False,
            }
        ],
        "notes": ["1 只标的没填成本，持仓盈亏只统计另一只"],
    }
    payload.update(overrides)
    return payload


def watchlist_payload(**overrides) -> dict:
    payload = {
        "type": "watchlist",
        "date": "2026-11-07",
        "generated_at": "2026-11-07 15:45:00",
        "summary": {
            "item_count": 2,
            "today_count": 2,
            "priced_count": 2,
            "missing_prev_close_count": 0,
            "up_count": 1,
            "down_count": 1,
            "flat_count": 0,
            "avg_change_percent": 0.5,
            "notional_per_item": 10000.0,
            "day_pnl": 100.0,
            "day_pnl_percent": 0.5,
            "yield_hit_count": 1,
            "price_hit_count": 0,
            "yield_near_count": 0,
            "price_near_count": 0,
        },
        "items": [
            {
                "id": 1,
                "code": "000651",
                "name": "格力电器",
                "asset_type": "股票",
                "price": 40.0,
                "prev_close": 39.0,
                "change": 1.0,
                "change_percent": 2.56,
                "notional": 10000.0,
                "notional_pnl": 256.41,
                "ttm_dividend": 2.0,
                "current_yield": 5.0,
                "target_yield": 4.5,
                "yield_gap_points": 0.5,
                "yield_trigger_price": 44.44,
                "yield_signal": "hit",
                "target_price": None,
                "price_gap_percent": None,
                "price_signal": "not_set",
                "snapshot_date": "2026-11-07",
                "is_today": True,
                "is_stale": False,
                "stale_days": 0,
            },
            {
                "id": 2,
                "code": "600900",
                "name": "长江电力",
                "asset_type": "股票",
                "price": 30.0,
                "prev_close": 31.0,
                "change": -1.0,
                "change_percent": -3.23,
                "notional": 10000.0,
                "notional_pnl": -322.58,
                "ttm_dividend": 1.0,
                "current_yield": 3.33,
                "target_yield": 4.5,
                "yield_gap_points": -1.17,
                "yield_trigger_price": 22.22,
                "yield_signal": "watch",
                "target_price": None,
                "price_gap_percent": None,
                "price_signal": "not_set",
                "snapshot_date": "2026-11-07",
                "is_today": True,
                "is_stale": False,
                "stale_days": 0,
            },
        ],
        "yield_hits": [
            {
                "code": "000651",
                "name": "格力电器",
                "current_yield": 5.0,
                "target_yield": 4.5,
                "yield_gap_points": 0.5,
            }
        ],
        "price_hits": [],
        "yield_near": [],
        "price_near": [],
        "notes": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def configured(monkeypatch):
    """把 SMTP 配成「已配置」，但不动真实网络（发送由 smtp_send 的假实现接住）。"""
    monkeypatch.setattr(settings, "mail_username", "825895587@qq.com")
    monkeypatch.setattr(settings, "mail_password", "abcdefghijklmnop")
    monkeypatch.setattr(settings, "mail_recipients", "825895587@qq.com")
    return settings


@pytest.fixture
def captured(monkeypatch):
    """接住要发出去的消息，不真的连 SMTP。"""
    box: dict = {}

    def fake_send(message: EmailMessage, config: report_mail.SmtpConfig) -> None:
        box["message"] = message
        box["config"] = config

    monkeypatch.setattr(report_mail, "smtp_send", fake_send)
    return box


def store_report(report_type: str, payload: dict, report_date: date, scope_id: int = 1) -> None:
    db = SessionLocal()
    try:
        db.add(
            DailyReport(
                report_type=report_type,
                scope_id=scope_id,
                report_date=report_date,
                title=f"{payload.get('type')} {report_date}",
                payload=json.dumps(payload, ensure_ascii=False),
                total_pnl=(payload.get("summary") or {}).get("day_pnl") or 0,
            )
        )
        db.commit()
    finally:
        db.close()


# ---------- 渲染 ----------

def test_render_contains_both_reports_and_key_numbers():
    text, html = render_daily_email(
        [portfolio_payload(), watchlist_payload()], date(2026, 11, 7)
    )

    assert "稳健组合" in html
    assert "观察池" in html
    assert "+¥1,000.00" in html          # 组合今日盈亏
    assert "+¥100.00" in html            # 观察池测算盈亏
    assert "招商银行" in html
    assert "格力电器" in html
    assert "股息达标" in html             # 观察池信号措辞
    assert "红利国企ETF国泰" in text
    assert "1 只标的没填成本" in text     # notes 要带过去


def test_render_shows_dash_when_pnl_unknown_not_zero():
    """昨收缺失 → day_pnl 是 None，邮件必须显示「—」，不能编成 ¥0.00。"""
    payload = portfolio_payload()
    payload["summary"]["day_pnl"] = None
    payload["summary"]["day_pnl_percent"] = None
    payload["summary"]["day_pnl_covered_count"] = 0

    text, html = render_daily_email([payload], date(2026, 11, 7))

    assert "¥0.00" not in html
    assert ">—<" in html
    assert "0/2 只有昨收" in html
    assert "今日盈亏 —" in text


def test_render_escapes_html_in_names():
    """标的名称来自用户输入，直接拼进 HTML 就是 XSS / 版面崩坏。"""
    payload = portfolio_payload()
    payload["items"][0]["name"] = '<img src=x onerror="alert(1)">'

    _, html = render_daily_email([payload], date(2026, 11, 7))

    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_render_empty_watchlist_still_says_so():
    payload = watchlist_payload()
    payload["items"] = []
    payload["yield_hits"] = []
    payload["summary"]["item_count"] = 0
    payload["summary"]["today_count"] = 0

    _, html = render_daily_email([payload], date(2026, 11, 7))

    assert "观察池为空" in html


def test_render_single_unknown_percent_omits_it():
    """只有一份组合日报 → 标题区展示涨跌幅；多份 → 不展示（百分比不能相加）。"""
    one = portfolio_payload()
    _, html_one = render_daily_email([one], date(2026, 11, 7))
    assert "+1.01%" in html_one

    second = portfolio_payload(portfolio={"id": 2, "name": "激进组合"})
    _, html_two = render_daily_email([one, second], date(2026, 11, 7))
    assert "组合合计" in html_two


# ---------- 主题 ----------

def test_subject_shows_both_sides():
    subject = build_subject(
        [portfolio_payload(), watchlist_payload()], date(2026, 11, 7)
    )

    assert subject.startswith("2026-11-07 投资日报")
    assert "组合 +¥1,000.00" in subject
    assert "观察池 +¥100.00" in subject


def test_subject_only_watchlist():
    subject = build_subject([watchlist_payload()], date(2026, 11, 7))

    assert "组合" not in subject
    assert "观察池 +¥100.00" in subject


def test_subject_without_pnl_is_just_headline():
    payload = watchlist_payload()
    payload["summary"]["day_pnl"] = None

    assert build_subject([payload], date(2026, 11, 7)) == "2026-11-07 投资日报"


# ---------- MIME ----------

def test_build_message_has_text_and_html_parts(configured):
    text, html = render_daily_email([portfolio_payload()], date(2026, 11, 7))
    message = build_message("[t] 主题", text, html, ["a@example.com"])

    assert message["To"] == "a@example.com"
    assert message["Subject"] == "[t] 主题"
    # From 必须与登录账号一致，否则 QQ / 163 直接拒信
    assert "825895587@qq.com" in str(message["From"])
    assert message.get_content_type() == "multipart/alternative"

    parts = message.get_payload()
    assert len(parts) == 2
    assert parts[0].get_content_type() == "text/plain"
    assert parts[1].get_content_type() == "text/html"
    assert "稳健组合" in parts[1].get_content()


def test_build_message_joins_multiple_recipients(configured):
    message = build_message("s", "t", "<p>h</p>", ["a@x.com", "b@x.com"])

    assert message["To"] == "a@x.com, b@x.com"


# ---------- 发送 ----------

def test_send_daily_reports_targets_today(configured, captured):
    store_report("portfolio", portfolio_payload(), today())
    store_report("watchlist", watchlist_payload(), today(), scope_id=0)

    db = SessionLocal()
    try:
        info = asyncio.run(send_daily_reports(db))
    finally:
        db.close()

    assert info["report_count"] == 2
    assert info["recipients"] == ["825895587@qq.com"]
    assert captured["message"] is not None
    assert "观察池" in captured["message"]["Subject"]
    assert captured["config"].host == "smtp.qq.com"
    assert captured["config"].port == 465


def test_send_daily_reports_ignores_other_days(configured, captured):
    store_report("portfolio", portfolio_payload(), date(2020, 1, 1))

    db = SessionLocal()
    try:
        with pytest.raises(NoReportsError):
            asyncio.run(send_daily_reports(db))
    finally:
        db.close()


def test_send_without_config_raises(monkeypatch, captured):
    monkeypatch.setattr(settings, "mail_username", "")
    monkeypatch.setattr(settings, "mail_password", "")
    monkeypatch.setattr(settings, "mail_recipients", "")

    db = SessionLocal()
    try:
        with pytest.raises(MailNotConfigured) as exc:
            asyncio.run(send_daily_reports(db))
    finally:
        db.close()

    assert "MAIL_USERNAME" in str(exc.value)
    assert "message" not in captured          # 连消息都不该组装


def test_smtp_failure_surfaces_as_mail_send_error(configured, monkeypatch):
    store_report("portfolio", portfolio_payload(), today())

    def boom(message, config):
        raise MailSendError("535 Authentication failed")

    monkeypatch.setattr(report_mail, "smtp_send", boom)

    db = SessionLocal()
    try:
        with pytest.raises(MailSendError):
            asyncio.run(send_daily_reports(db))
    finally:
        db.close()


# ---------- 调度任务 ----------

def test_job_skips_when_not_configured(db_session, monkeypatch):
    from scheduler import run_daily_report_mail

    monkeypatch.setattr(settings, "mail_username", "")
    result = asyncio.run(run_daily_report_mail(db_session))

    assert result.total == 0
    assert result.status == "skipped"
    assert "未配置邮件" in result.message


def test_job_fails_when_no_reports(db_session, configured):
    from scheduler import run_daily_report_mail

    result = asyncio.run(run_daily_report_mail(db_session))

    assert result.status == "failed"
    assert "还没有生成日报" in result.message


def test_job_reports_success(db_session, configured, captured):
    from scheduler import run_daily_report_mail

    store_report("portfolio", portfolio_payload(), today())

    result = asyncio.run(run_daily_report_mail(db_session))

    assert result.status == "success"
    assert "已发送 1 份日报" in result.message
    assert "825895587@qq.com" in result.message
    assert captured["message"] is not None


# ---------- 配置接口 ----------

def test_mail_config_hides_password_and_masks_account(client, monkeypatch):
    monkeypatch.setattr(settings, "mail_username", "825895587@qq.com")
    monkeypatch.setattr(settings, "mail_password", "supersecretvalue")
    monkeypatch.setattr(settings, "mail_recipients", "825895587@qq.com")

    data = client.get("/api/admin/mail").json()

    assert data["configured"] is True
    assert data["username"] == "825***@qq.com"
    assert data["schedule_time"] == "15:50"
    assert data["recipients"] == ["825895587@qq.com"]
    assert "supersecretvalue" not in json.dumps(data, ensure_ascii=False)


def test_mail_config_lists_missing_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "mail_username", "")
    monkeypatch.setattr(settings, "mail_password", "")
    monkeypatch.setattr(settings, "mail_recipients", "")

    data = client.get("/api/admin/mail").json()

    assert data["configured"] is False
    assert set(data["missing"]) == {"MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_RECIPIENTS"}
    # note 只讲这张卡片干什么；「缺什么」由 missing 承载，避免同一句话出现两次
    assert "交易日 15:50" in data["note"]


def test_mail_send_returns_409_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "mail_username", "")
    monkeypatch.setattr(settings, "mail_password", "")
    monkeypatch.setattr(settings, "mail_recipients", "")

    res = client.post("/api/admin/mail/send")

    assert res.status_code == 409
    assert "MAIL_USERNAME" in res.json()["detail"]


def test_mail_send_returns_404_when_day_has_no_reports(client, configured, captured):
    res = client.post("/api/admin/mail/send", params={"report_date": "2020-01-01"})

    assert res.status_code == 404
    assert "还没有生成日报" in res.json()["detail"]


def test_mail_send_returns_502_on_smtp_error(client, configured, monkeypatch):
    store_report("portfolio", portfolio_payload(), today())

    def boom(message, config):
        raise MailSendError("535 Authentication failed")

    monkeypatch.setattr(report_mail, "smtp_send", boom)

    res = client.post("/api/admin/mail/send")

    assert res.status_code == 502
    assert "535" in res.json()["detail"]


def test_mail_send_success_payload(client, configured, captured):
    store_report("portfolio", portfolio_payload(), today())
    store_report("watchlist", watchlist_payload(), today(), scope_id=0)

    res = client.post("/api/admin/mail/send")

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["report_count"] == 2
    assert body["recipients"] == ["825895587@qq.com"]
    assert body["subject"].startswith(today().isoformat())


# ---------- SMTP 错误翻译 ----------

class FakeSmtpClient:
    """只实现 smtp_send 用到的那几个方法，用来验证错误翻译与调用顺序。"""

    def __init__(self, *, login_error=None):
        self.login_error = login_error
        self.sent = None
        self.quit_called = False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        if self.login_error:
            raise self.login_error

    def send_message(self, message):
        self.sent = message

    def quit(self):
        self.quit_called = True


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(report_mail.smtplib, "SMTP_SSL", lambda *a, **kw: client)


def _config():
    return report_mail.SmtpConfig(
        host="smtp.qq.com",
        port=465,
        use_ssl=True,
        username="825895587@qq.com",
        password="x",
        timeout=5,
    )


def _message():
    return build_message("s", "t", "<p>h</p>", ["825895587@qq.com"])


def test_smtp_send_success_sends_and_quits(monkeypatch):
    client = FakeSmtpClient()
    _patch_client(monkeypatch, client)

    report_mail.smtp_send(_message(), _config())

    assert client.sent is not None
    assert client.quit_called is True


def test_smtp_disconnected_on_login_points_at_auth_code(monkeypatch):
    """QQ 在授权码错误时直接断连（不给 535），错误文案必须仍然指向授权码。"""
    import smtplib as smtplib_module

    client = FakeSmtpClient(
        login_error=smtplib_module.SMTPServerDisconnected("Connection unexpectedly closed")
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(MailSendError) as exc:
        report_mail.smtp_send(_message(), _config())

    assert "授权码" in str(exc.value)
    assert client.quit_called is True          # 出错也要收尾，别留半开连接


def test_smtp_auth_error_message_mentions_code(monkeypatch):
    import smtplib as smtplib_module

    client = FakeSmtpClient(
        login_error=smtplib_module.SMTPAuthenticationError(535, b"Authentication failed")
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(MailSendError) as exc:
        report_mail.smtp_send(_message(), _config())

    assert "535" in str(exc.value)
    assert "授权码" in str(exc.value)


def test_smtp_connect_failure_is_readable(monkeypatch):
    import smtplib as smtplib_module

    def refuse(*args, **kwargs):
        raise OSError("Connection refused")

    monkeypatch.setattr(report_mail.smtplib, "SMTP_SSL", refuse)

    with pytest.raises(MailSendError) as exc:
        report_mail.smtp_send(_message(), _config())

    assert "连不上 smtp.qq.com:465" in str(exc.value)


def test_empty_portfolio_collapses_to_one_line():
    """空组合不能画 6 张全「—」的 KPI 卡，把有内容的组合挤出屏幕。"""
    payload = portfolio_payload()
    payload["items"] = []
    payload["summary"]["asset_count"] = 0
    payload["summary"]["day_pnl"] = None
    # 真实生成时空组合的 notes 就是这一句
    payload["notes"] = ["该组合还没有资产，日报只包含空快照"]

    text, html = render_daily_email([payload], date(2026, 11, 7))

    assert "该组合还没有资产" in html
    assert "持仓市值" not in html          # KPI 卡片不该出现
    assert "（该组合还没有资产）" in text


def test_empty_portfolio_without_notes_still_says_something():
    """notes 为空（旧报告）也不能变成一个只有标题的空区块。"""
    payload = portfolio_payload()
    payload["items"] = []
    payload["notes"] = []

    _, html = render_daily_email([payload], date(2026, 11, 7))

    assert "该组合还没有资产，日报只包含空快照" in html
