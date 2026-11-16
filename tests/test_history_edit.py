"""历史库的编辑能力：今日统计 / 删除 / 清空 / 撤销恢复。

判据都翻译成用户能感知的行为：
- 今日统计只算本地日历日内的记录，速度是「字/分」且空队列为 0；
- 删除/清空返回被删记录，撤销能把原文、字数、时间原样写回；
- 恢复后「最新在前」的顺序与删除前一致（不能因为重插 id 而倒序）。
"""

from datetime import datetime, timedelta

import pytest

from phonemic.server import history as history_mod
from phonemic.server.history import HistoryStore


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(str(tmp_path / "history.db"))
    yield s
    s.close()


def _insert_at(store, text, when):
    """直接写一条指定时刻的记录（绕过 add_message 的「现在」时间戳）。"""
    chars = len("".join(text.split()))
    with history_mod._lock:
        cur = store._conn.execute(
            "INSERT INTO message_log(created_at, text, chars, client_ip, kind) "
            "VALUES (?, ?, ?, '', 'text')",
            (when.astimezone().isoformat(timespec="seconds"), text, chars),
        )
        store._conn.commit()
        return int(cur.lastrowid)


def test_today_stats_counts_only_local_today(store):
    now = datetime.now().astimezone()
    _insert_at(store, "今天的一句话", now)
    _insert_at(store, "昨天的旧记录", now - timedelta(days=1))

    stats = store.today_stats()
    assert stats["count"] == 1
    assert stats["chars"] == len("今天的一句话")
    # 全量统计仍包含昨天那条
    assert store.stats()["total"] == 2


def test_today_stats_rate_is_chars_per_minute_and_zero_when_empty(store):
    assert store.today_stats()["rate"] == 0.0
    assert store.today_stats()["count"] == 0

    now = datetime.now().astimezone()
    _insert_at(store, "一二三四五六七八九十", now - timedelta(minutes=4))
    stats = store.today_stats()
    assert stats["chars"] == 10
    # 10 字 / 约 4 分钟 ≈ 2.5 字/分（允许少量计时误差）
    assert 1.5 <= stats["rate"] <= 3.5


def test_delete_message_returns_record_and_removes_it(store):
    rec = store.add_message("要删的话")
    removed = store.delete_message(rec["id"])
    assert removed is not None
    assert removed["text"] == "要删的话"
    assert store.recent_messages() == []
    assert store.delete_message(rec["id"]) is None


def test_clear_returns_all_newest_first(store):
    for i in range(3):
        store.add_message(f"第{i}条")
    removed = store.clear_messages()
    assert [r["text"] for r in removed] == ["第2条", "第1条", "第0条"]
    assert store.stats()["total"] == 0


def test_restore_after_clear_keeps_text_chars_time_and_order(store):
    for i in range(3):
        store.add_message(f"第{i}条")
    original = store.recent_messages()
    removed = store.clear_messages()

    n = store.restore_messages(removed)
    assert n == 3
    restored = store.recent_messages()
    assert [r["text"] for r in restored] == [r["text"] for r in original]
    assert [r["chars"] for r in restored] == [r["chars"] for r in original]
    assert [r["created_at"] for r in restored] == [r["created_at"] for r in original]


def test_restore_single_deleted_record(store):
    a = store.add_message("保留")
    b = store.add_message("删掉再恢复")
    removed = store.delete_message(b["id"])
    store.restore_messages([removed])
    texts = [r["text"] for r in store.recent_messages()]
    assert texts == ["删掉再恢复", "保留"]
    assert store.stats()["total"] == 2


def test_restore_empty_is_noop(store):
    assert store.restore_messages([]) == 0
    assert store.stats()["total"] == 0