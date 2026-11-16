"""输入历史库（SQLite）的行为测试。

这里只测用户/界面能感知的约定：
- 最新在前，分页不重不漏；
- 字数是「非空白字符」，原文保留换行/空格；
- 设备数按 IP+UA 去重，一次连接一行、断开时间只写一次；
- 时间戳是可被前端 Date 解析的本地时区 ISO。
"""

from datetime import datetime

import pytest

from phonemic.server.history import HistoryStore


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(str(tmp_path / "history.db"))
    yield s
    s.close()


def test_recent_messages_newest_first_and_paginated(store):
    ids = [store.add_message(f"第{i}条")["id"] for i in range(5)]

    page1 = store.recent_messages(limit=2, offset=0)
    page2 = store.recent_messages(limit=2, offset=2)
    page3 = store.recent_messages(limit=2, offset=4)

    assert [m["text"] for m in page1] == ["第4条", "第3条"]
    assert [m["text"] for m in page2] == ["第2条", "第1条"]
    assert [m["text"] for m in page3] == ["第0条"]
    assert [m["id"] for m in page1 + page2 + page3] == list(reversed(ids))


def test_chars_count_ignores_whitespace_but_text_is_verbatim(store):
    record = store.add_message("  你好，\n世界！\tOK  ")
    assert record["text"] == "  你好，\n世界！\tOK  "
    assert record["chars"] == 8          # 你好，世界！OK = 3+3+2
    assert store.stats()["total_chars"] == 8


def test_created_at_is_parseable_local_iso(store):
    record = store.add_message("时间")
    parsed = datetime.fromisoformat(record["created_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def test_devices_are_distinct_by_ip_and_user_agent(store):
    a1 = store.start_session("10.0.0.2", "UA-A", "xsalsa20")
    store.end_session(a1)
    # 同一设备的第二次连接：仍只算一台
    a2 = store.start_session("10.0.0.2", "UA-A", "xchacha20")
    store.end_session(a2)
    store.start_session("10.0.0.2", "UA-B")       # 同 IP 换设备 = 另一台
    store.start_session("10.0.0.3", "UA-A")       # 同 UA 换 IP = 另一台

    assert store.stats()["devices"] == 3
    sessions = store.recent_sessions(10)
    assert len(sessions) == 4
    assert sessions[0]["ip"] == "10.0.0.3"
    assert sessions[0]["disconnected_at"] == ""


def test_end_session_is_idempotent_and_keeps_first_disconnect_time(store):
    sid = store.start_session("10.0.0.9", "UA")
    store.end_session(sid)
    first = store.recent_sessions(1)[0]["disconnected_at"]
    assert first

    store.end_session(sid)
    again = store.recent_sessions(1)[0]["disconnected_at"]
    assert again == first
