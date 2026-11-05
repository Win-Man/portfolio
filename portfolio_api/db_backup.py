"""SQLite 数据库体积统计与在线备份。

两个关键取舍：

1. **备份用 `Connection.backup()`，不用 `shutil.copy`**
   直接复制文件，有可能拷到「主库已写、回滚日志还没落」的中间态，
   产出一个**打不开或数据不一致**的 .db。`backup()` 是 SQLite 官方的
   在线备份 API：逐页复制、遇到写锁自动 sleep 重试，产出事务一致的快照。
   本项目后台有 scheduler 线程在写入（15:05 刷新价格、15:45 出日报），
   这一点尤其重要。

2. **按表占用靠 `dbstat` 虚拟表，不做估算**
   `dbstat` 需要编译期开启 `SQLITE_ENABLE_DBSTAT_VTAB`（Python 官方 wheel
   是开的，少数精简构建没有）。取不到时降级为「不提供按表明细」，
   而不是让整个接口 500 —— 总大小才是用户真正要看的。

时间是 Asia/Shanghai 的墙上时间（naive），与全站其它接口口径一致。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from config import settings

TZ = ZoneInfo("Asia/Shanghai")

# 只认本模块生成的文件名：portfolio-20261105-211530.db（同一秒内重复备份带 -1/-2 后缀）。
# 这条正则同时承担**路径穿越防护**：不匹配就不允许下载/删除。
BACKUP_PREFIX = "portfolio-"
BACKUP_SUFFIX = ".db"
BACKUP_NAME_RE = re.compile(r"^portfolio-\d{8}-\d{6}(?:-\d+)?\.db$")

_UNITS = ("B", "KB", "MB", "GB", "TB")


class DatabaseUnavailable(RuntimeError):
    """数据库文件不存在 / 不是 sqlite / 不支持的 URL。"""


class BackupFailed(RuntimeError):
    """备份过程失败（含写盘失败、完整性校验未通过）。"""


class BackupNotFound(LookupError):
    """备份文件不存在。"""


class InvalidBackupName(ValueError):
    """文件名不符合命名规则（可能带路径成分）。"""


# ---------- 路径解析 ----------

def database_file() -> Optional[Path]:
    """从 `settings.database_url` 解析出 sqlite 文件路径。

    非 sqlite URL、`:memory:`、解析失败都返回 None —— 调用方据此降级，
    而不是抛异常让整个「后台管理」页挂掉（同一页还有定时任务要看）。
    """
    try:
        url = make_url(settings.database_url)
    except ArgumentError:
        return None
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    # 与 SQLAlchemy 一致：sqlite 的相对路径按**当前工作目录**解析
    return Path(url.database).expanduser().resolve()


def backup_directory() -> Path:
    return Path(settings.backup_dir).expanduser().resolve()


def human_size(num: float) -> str:
    """字节数转人读文本。1 KB 以下不显示小数（"512 B" 而不是 "512.0 B"）。"""
    size = float(num or 0)
    if size < 1024:
        return f"{int(size)} B"
    for unit in _UNITS[1:]:
        size /= 1024
        if size < 1024 or unit == _UNITS[-1]:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"


# ---------- 体积统计 ----------

def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_sizes(conn: sqlite3.Connection) -> List[Dict]:
    """按表聚合占用（表数据 + 它自己的索引）。

    `dbstat` 是**按对象**一行（表一行、每个索引各一行），直接把原始结果
    丢给前端会看到 `ix_watchlist_snapshots_id` 这种主儿，用户不关心。
    这里用 `sqlite_master` 把索引归并到所属表，再看整体排序。
    """
    index_owner = {
        name: table
        for name, table in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'"
        )
    }
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    buckets: Dict[str, int] = {name: 0 for name in tables}
    for name, pagesize in conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
        owner = index_owner.get(name, name)
        if owner in buckets:  # sqlite_schema 等内部对象直接丢弃
            buckets[owner] += int(pagesize or 0)

    result: List[Dict] = []
    for name in sorted(buckets, key=lambda n: (-buckets[n], n)):
        try:
            rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0])
        except sqlite3.Error:
            rows = 0  # 行数只是参考信息，数不出来不该让接口失败
        result.append({"name": name, "rows": rows, "size_bytes": buckets[name]})
    return result


def database_overview(backups: Optional[List[Dict]] = None) -> Dict:
    """数据库体积概况 + 备份目录概况。备份列表可由调用方传入，避免重复扫目录。"""
    path = database_file()
    directory = backup_directory()
    backups = list_backups() if backups is None else backups

    info: Dict = {
        "supported": path is not None,
        "path": str(path) if path else "",
        "file_name": path.name if path else "",
        "exists": bool(path and path.is_file()),
        "size_bytes": 0,
        "page_size": 0,
        "page_count": 0,
        "free_pages": 0,
        "reclaimable_bytes": 0,
        "wal_size_bytes": 0,
        "journal_mode": "",
        "tables": [],
        "tables_supported": False,
        "backup_dir": str(directory),
        "backup_count": len(backups),
        "backup_size_bytes": sum(b["size_bytes"] for b in backups),
        "latest_backup": backups[0] if backups else None,
    }
    if not path or not path.is_file():
        return info

    info["size_bytes"] = path.stat().st_size
    wal = path.with_name(path.name + "-wal")
    if wal.is_file():
        info["wal_size_bytes"] = wal.stat().st_size

    try:
        # timeout 给 3 秒：正赶上后台任务写入时等一小会儿，而不是立刻抛 locked
        conn = sqlite3.connect(str(path), timeout=3.0)
    except sqlite3.Error:
        return info

    try:
        info["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        info["page_count"] = int(conn.execute("PRAGMA page_count").fetchone()[0])
        info["free_pages"] = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        info["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        # 空闲页是「删了数据但文件没变小」的部分，VACUUM 能还回来
        info["reclaimable_bytes"] = info["free_pages"] * info["page_size"]
        try:
            info["tables"] = _table_sizes(conn)
            info["tables_supported"] = True
        except sqlite3.Error:
            info["tables"] = []
            info["tables_supported"] = False
    except (sqlite3.Error, TypeError):
        # 文件在但不是合法库 / 读 PRAGMA 失败：只报大小，不报结构
        pass
    finally:
        conn.close()

    return info


# ---------- 备份 ----------

def list_backups() -> List[Dict]:
    """备份列表，新的在前。只认本模块生成的文件名。"""
    directory = backup_directory()
    if not directory.is_dir():
        return []

    items: List[Dict] = []
    for entry in directory.iterdir():
        if not BACKUP_NAME_RE.match(entry.name) or not entry.is_file():
            continue
        stat = entry.stat()
        items.append(
            {
                "name": entry.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, TZ).replace(tzinfo=None),
            }
        )
    items.sort(key=lambda x: (x["created_at"], x["name"]), reverse=True)
    return items


def create_backup() -> Dict:
    """在线备份一份，返回新备份的信息。失败时不留半成品文件。"""
    source = database_file()
    if source is None or not source.is_file():
        raise DatabaseUnavailable("数据库文件不存在，无法备份")

    directory = backup_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupFailed(f"无法创建备份目录 {directory}：{exc}") from exc

    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    target = directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    seq = 1
    while target.exists():  # 同一秒连点两次：加序号，绝不覆盖已有备份
        target = directory / f"{BACKUP_PREFIX}{stamp}-{seq}{BACKUP_SUFFIX}"
        seq += 1

    source_conn = sqlite3.connect(str(source), timeout=10.0)
    target_conn: Optional[sqlite3.Connection] = None
    try:
        target_conn = sqlite3.connect(str(target))
        source_conn.backup(target_conn)
        row = target_conn.execute("PRAGMA quick_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            # 坏备份比没有备份更危险：真要用的时候才发现就晚了
            raise BackupFailed(f"备份完整性校验未通过：{row[0] if row else '无结果'}")
    except BackupFailed:
        target.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError) as exc:
        target.unlink(missing_ok=True)
        raise BackupFailed(f"备份失败：{exc}") from exc
    finally:
        if target_conn is not None:
            target_conn.close()
        source_conn.close()

    stat = target.stat()
    return {
        "name": target.name,
        "size_bytes": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, TZ).replace(tzinfo=None),
    }


def resolve_backup(name: str) -> Path:
    """把文件名解析成绝对路径，顺便挡掉一切非法输入。"""
    if not BACKUP_NAME_RE.match(name):
        raise InvalidBackupName(name)
    directory = backup_directory()
    path = (directory / name).resolve()
    if path.parent != directory:  # 双保险：正则已经不允许路径分隔符
        raise InvalidBackupName(name)
    if not path.is_file():
        raise BackupNotFound(name)
    return path


def delete_backup(name: str) -> None:
    resolve_backup(name).unlink()
