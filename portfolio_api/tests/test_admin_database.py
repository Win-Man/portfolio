"""后台管理：SQLite 体积统计与备份。

覆盖点：
- 概况接口的字段口径（大小 / 页 / 按表占用）
- 索引占用要归并到所属表，而不是以 `ix_xxx` 的形式冒出来
- 备份真的可用：文件大小对得上、能被 sqlite3 打开、quick_check 通过
- 文件名校验挡得住路径穿越与非本工具生成的文件
- 备份目录可配置（测试不污染仓库里的 backups/）
"""
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

import db_backup
from config import settings
from models import Asset


@pytest.fixture
def backup_dir(monkeypatch):
    """把备份目录指到独立临时目录。`db_backup.backup_directory()` 每次都读 settings，
    所以改 settings 就够了。

    不用 pytest 的 `tmp_path`：它的根目录（`pytest-of-<user>`）在部分沙箱环境里
    已存在，重复 mkdir 会被判成 EEXIST。`mkdtemp` 每次生成全新目录，绕开这个坑。
    """
    root = Path(tempfile.mkdtemp(prefix="pf-backup-"))
    target = root / "backups"
    monkeypatch.setattr(settings, "backup_dir", str(target))
    yield target
    shutil.rmtree(root, ignore_errors=True)


# ---------- 体积概况 ----------

def test_overview_reports_size_and_pages(client, backup_dir):
    data = client.get("/api/admin/database").json()

    assert data["supported"] is True
    assert data["exists"] is True
    assert data["size_bytes"] > 0
    # 测试库是临时文件，名字不叫 portfolio.db；只要求路径与文件名自洽
    assert data["file_name"].endswith(".db")
    assert data["path"].endswith(data["file_name"])
    # 页数与大小要自洽
    assert data["page_size"] > 0
    assert data["page_count"] > 0
    assert data["page_count"] * data["page_size"] <= data["size_bytes"]
    assert data["reclaimable_bytes"] == data["free_pages"] * data["page_size"]
    assert data["backup_count"] == 0
    assert data["latest_backup"] is None


def test_overview_table_sizes_merge_indexes(client, db_session, backup_dir):
    """dbstat 是「表一行 + 每个索引一行」，接口要把索引归并回表。"""
    data = client.get("/api/admin/database").json()
    if not data["tables_supported"]:
        pytest.skip("当前 SQLite 构建未启用 dbstat")

    names = [row["name"] for row in data["tables"]]
    assert "assets" in names
    # 索引不能单独出现（那是实现细节，用户不关心）
    assert not any(name.startswith("ix_") or name.startswith("sqlite_") for name in names)

    # 行数必须与真实表一致 —— 写死数字会让种子数据一变就红
    expected_rows = db_session.query(Asset).count()
    assets = next(row for row in data["tables"] if row["name"] == "assets")
    assert assets["rows"] == expected_rows > 0
    assert assets["size_bytes"] > 0


def test_overview_degrades_when_database_missing(client, backup_dir, monkeypatch):
    """库文件不存在时返回 200 + exists=false，不能整页 500。"""
    monkeypatch.setattr(settings, "database_url", "sqlite:////nonexistent/pf-not-here.db")
    data = client.get("/api/admin/database").json()

    assert data["exists"] is False
    assert data["size_bytes"] == 0
    assert data["tables"] == []


def test_overview_rejects_non_sqlite_url(client, backup_dir, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://user@localhost/db")
    data = client.get("/api/admin/database").json()

    assert data["supported"] is False
    assert data["path"] == ""


# ---------- 备份 ----------

def test_create_backup_produces_usable_file(client, db_session, backup_dir):
    res = client.post("/api/admin/database/backups")
    assert res.status_code == 201

    info = res.json()
    assert db_backup.BACKUP_NAME_RE.match(info["name"])
    path = backup_dir / info["name"]
    assert path.is_file()
    assert path.stat().st_size == info["size_bytes"] > 0

    # 备份出来的是完整可用的库，不是半截镜像
    expected_assets = db_session.query(Asset).count()
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"assets", "portfolios", "daily_reports"} <= tables
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == expected_assets
    finally:
        conn.close()


def test_backup_updates_overview(client, backup_dir):
    assert client.get("/api/admin/database").json()["backup_count"] == 0

    info = client.post("/api/admin/database/backups").json()
    data = client.get("/api/admin/database").json()

    assert data["backup_count"] == 1
    assert data["backup_size_bytes"] == info["size_bytes"]
    assert data["latest_backup"]["name"] == info["name"]


def test_two_backups_in_a_row_never_overwrite(client, backup_dir):
    """同一秒内连点两次要加序号，不能覆盖已有备份。"""
    first = client.post("/api/admin/database/backups").json()
    second = client.post("/api/admin/database/backups").json()

    assert first["name"] != second["name"]
    assert len({p.name for p in backup_dir.iterdir()}) == 2
    assert (backup_dir / first["name"]).is_file()
    assert (backup_dir / second["name"]).is_file()


def test_backup_list_is_newest_first(client, backup_dir):
    names = [client.post("/api/admin/database/backups").json()["name"] for _ in range(3)]
    data = client.get("/api/admin/database/backups").json()

    assert data["total"] == 3
    assert [row["name"] for row in data["items"]] == list(reversed(names))
    assert data["total_size_bytes"] == sum(row["size_bytes"] for row in data["items"])


# ---------- 下载 / 删除 / 安全 ----------

def test_download_returns_attachment(client, backup_dir):
    name = client.post("/api/admin/database/backups").json()["name"]
    res = client.get(f"/api/admin/database/backups/{name}")

    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert name in res.headers["content-disposition"]
    assert len(res.content) == (backup_dir / name).stat().st_size
    assert res.content[:16] == b"SQLite format 3\x00"  # 文件头，确认没被当作文本处理


def test_delete_removes_file_then_404(client, backup_dir):
    name = client.post("/api/admin/database/backups").json()["name"]

    assert client.delete(f"/api/admin/database/backups/{name}").status_code == 200
    assert not (backup_dir / name).exists()
    assert client.delete(f"/api/admin/database/backups/{name}").status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "evil.db",                       # 不是本工具生成的命名
        "portfolio-20261105-211209.txt",  # 后缀不对
        "portfolio-2026110-211209.db",    # 日期位数不对
        "portfolio.db",                   # 就是源库本身
    ],
)
def test_illegal_backup_name_rejected(client, backup_dir, name):
    """非法命名 → 400，且绝不能读到备份目录之外的东西。"""
    assert client.get(f"/api/admin/database/backups/{name}").status_code == 400
    assert client.delete(f"/api/admin/database/backups/{name}").status_code == 400
    with pytest.raises(db_backup.InvalidBackupName):
        db_backup.resolve_backup(name)


def test_path_traversal_cannot_escape_backup_dir(backup_dir):
    """即便绕过 HTTP 层直接调库函数，也不允许跳出备份目录。"""
    for name in ["../portfolio.db", "../../portfolio.db", "sub/portfolio-20260101-000000.db"]:
        with pytest.raises(db_backup.InvalidBackupName):
            db_backup.resolve_backup(name)


def test_missing_backup_returns_404(client, backup_dir):
    res = client.get("/api/admin/database/backups/portfolio-20200101-000000.db")
    assert res.status_code == 404


# ---------- 工具函数 ----------

@pytest.mark.parametrize(
    "num,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (5214208, "5.0 MB"),
        (1024 ** 3, "1.0 GB"),
    ],
)
def test_human_size(num, expected):
    assert db_backup.human_size(num) == expected
