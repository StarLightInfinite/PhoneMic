"""输入历史与设备连接记录（SQLite）。

设计取舍：
- **一份库、两端读**：手机端历史面板与 PC 主界面读同一张表，避免「手机有、电脑没有」
  的两套真相。数据库放在用户数据目录（%LOCALAPPDATA%\\PhoneMic\\history.db），
  与配置同层，卸载/备份时一起带走。
- **只记最终发送的文本**：preview 是识别中的半截词，逐帧落库既没有价值又会把库撑爆；
  文件/图片有自己的传输链路，也不写进文本历史。
- **字数按「非空白字符」计**：中文场景下这是用户真正关心的量；同时保留原文，换行/空格
  不丢。
- **连接一次留一行**：设备信息（IP/UA/算法/起止时间）用于主界面的「连接了多少台、
  最近用过哪些设备」。活动连接数由 ConnectionManager 维护，历史设备数从本表统计。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from phonemic.utils.paths import get_config_dir

_lock = threading.RLock()
_store: Optional["HistoryStore"] = None

# 测试/便携模式：用环境变量把库指到临时文件，避免污染真实用户历史
_ENV_DB = "PHONEMIC_HISTORY_DB"


def now_iso() -> str:
    """本地时区 ISO 时间（秒精度），直接给前端 Date 解析。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _count_chars(text: str) -> int:
    """按非空白字符计数：中文标点算字，空格/换行不算。"""
    return len("".join(text.split()))


def _row_to_message(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "text": row["text"],
        "chars": row["chars"],
        "client_ip": row["client_ip"] or "",
        "kind": row["kind"] or "text",
    }


def _row_to_session(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "ip": row["ip"] or "",
        "user_agent": row["user_agent"] or "",
        "algorithm": row["algorithm"] or "",
        "connected_at": row["connected_at"] or "",
        "disconnected_at": row["disconnected_at"] or "",
    }


class HistoryStore:
    """线程安全的轻量 SQLite 封装（单连接 + 锁 + WAL）。

    服务端在 uvicorn 线程写，Qt 主线程读；写操作都很小（一条 insert），
    单连接配锁足够，也避免了每帧新建连接的开销。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        env_path = os.environ.get(_ENV_DB)
        db_path = path or env_path or str(get_config_dir().parent / "history.db")
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            self._conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()

    def _init_schema(self) -> None:
        with _lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS message_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    chars INTEGER NOT NULL,
                    client_ip TEXT,
                    kind TEXT NOT NULL DEFAULT 'text'
                );
                CREATE INDEX IF NOT EXISTS idx_message_log_created
                    ON message_log(created_at DESC);
                CREATE TABLE IF NOT EXISTS client_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT,
                    user_agent TEXT,
                    algorithm TEXT,
                    connected_at TEXT NOT NULL,
                    disconnected_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_client_sessions_connected
                    ON client_sessions(connected_at DESC);
                """
            )
            self._conn.commit()

    # ---- 消息 ----

    def add_message(self, text: str, client_ip: str = "", kind: str = "text") -> Dict[str, Any]:
        """记录一条最终发送的文本，返回与前端/PC 共享的记录结构。"""
        now = now_iso()
        chars = _count_chars(text)
        with _lock:
            cur = self._conn.execute(
                "INSERT INTO message_log(created_at, text, chars, client_ip, kind) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, text, chars, client_ip or "", kind or "text"),
            )
            self._conn.commit()
            return {
                "id": int(cur.lastrowid),
                "created_at": now,
                "text": text,
                "chars": chars,
                "client_ip": client_ip or "",
                "kind": kind or "text",
            }

    def recent_messages(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """按时间倒序返回历史文本（最新在前）。"""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with _lock:
            rows = self._conn.execute(
                "SELECT id, created_at, text, chars, client_ip, kind "
                "FROM message_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_message(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        """历史总量：条数、总字数、历史设备数（按 IP+UA 去重）。"""
        with _lock:
            msg = self._conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(chars), 0) AS chars FROM message_log"
            ).fetchone()
            dev = self._conn.execute(
                "SELECT COUNT(*) AS n FROM ("
                "  SELECT DISTINCT ip, user_agent FROM client_sessions"
                ") t"
            ).fetchone()
        return {
            "total": int(msg["total"] or 0),
            "total_chars": int(msg["chars"] or 0),
            "devices": int(dev["n"] or 0),
        }

    def today_stats(self) -> Dict[str, Any]:
        """今日条数 / 今日字数 / 输入速度（字/分）。

        「今天」按本地时区的日历日切（created_at 存的就是本地 ISO，取前 10 位
        与今天比对即可，无需逐行解析）。速度＝今日字数 ÷ 今日首条到现在的分钟数，
        没有记录时为 0——空队列给 0 而不是除零。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        with _lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(chars), 0) AS chars, "
                "MIN(created_at) AS first FROM message_log "
                "WHERE substr(created_at, 1, 10) = ?",
                (today,),
            ).fetchone()
        count = int(row["n"] or 0)
        chars = int(row["chars"] or 0)
        rate = 0.0
        first = row["first"]
        if count and first:
            try:
                started = datetime.fromisoformat(first)
                minutes = max(
                    1.0, (datetime.now().astimezone() - started).total_seconds() / 60.0)
                rate = round(chars / minutes, 1)
            except ValueError:
                rate = 0.0
        return {"count": count, "chars": chars, "rate": rate}

    def delete_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """删除一条历史，返回被删的记录（供「撤销」原样恢复）；不存在返回 None。"""
        with _lock:
            row = self._conn.execute(
                "SELECT id, created_at, text, chars, client_ip, kind "
                "FROM message_log WHERE id = ?",
                (int(message_id),),
            ).fetchone()
            if row is None:
                return None
            record = _row_to_message(row)
            self._conn.execute("DELETE FROM message_log WHERE id = ?", (int(message_id),))
            self._conn.commit()
            return record

    def clear_messages(self) -> List[Dict[str, Any]]:
        """清空全部历史，返回被删记录（最新在前），供「撤销」整批恢复。"""
        with _lock:
            rows = self._conn.execute(
                "SELECT id, created_at, text, chars, client_ip, kind "
                "FROM message_log ORDER BY id DESC"
            ).fetchall()
            records = [_row_to_message(r) for r in rows]
            self._conn.execute("DELETE FROM message_log")
            self._conn.commit()
            return records

    def restore_messages(self, records: List[Dict[str, Any]]) -> int:
        """把一批记录原样写回（保留原文/字数/时间/来源），返回恢复条数。

        撤销清空/删除时用：按传入顺序的**反序**插入，让最新的记录拿到最大的 id，
        恢复后「最新在前」的排序与删除前一致。id 重新分配（旧 id 可能已被占用）。
        """
        if not records:
            return 0
        n = 0
        with _lock:
            for rec in reversed(records):
                text = str(rec.get("text") or "")
                self._conn.execute(
                    "INSERT INTO message_log(created_at, text, chars, client_ip, kind) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        rec.get("created_at") or now_iso(),
                        text,
                        int(rec.get("chars") or _count_chars(text)),
                        rec.get("client_ip") or "",
                        rec.get("kind") or "text",
                    ),
                )
                n += 1
            self._conn.commit()
        return n

    # ---- 设备会话 ----

    def start_session(self, ip: str, user_agent: str, algorithm: str = "") -> int:
        with _lock:
            cur = self._conn.execute(
                "INSERT INTO client_sessions(ip, user_agent, algorithm, connected_at) "
                "VALUES (?, ?, ?, ?)",
                (ip or "", user_agent or "", algorithm or "", now_iso()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def end_session(self, session_id: Optional[int]) -> None:
        if not session_id:
            return
        with _lock:
            self._conn.execute(
                "UPDATE client_sessions SET disconnected_at=? "
                "WHERE id=? AND disconnected_at IS NULL",
                (now_iso(), int(session_id)),
            )
            self._conn.commit()

    def recent_sessions(self, limit: int = 5) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        with _lock:
            rows = self._conn.execute(
                "SELECT id, ip, user_agent, algorithm, connected_at, disconnected_at "
                "FROM client_sessions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def close(self) -> None:
        with _lock:
            try:
                self._conn.close()
            except Exception:
                pass


def get_history_store() -> HistoryStore:
    """进程内单例。"""
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store
