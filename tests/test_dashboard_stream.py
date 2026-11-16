"""中间输入流的新增能力：搜索 / 筛选 / 今日统计 / 删除 / 清空 / 撤销 / 导出 / 重发。

每条都翻译成用户能感知的行为（见 conftest 准入三问）：
- 搜关键词只剩匹配的那几条；切「今天」只剩今天的；
- 删除/清空后列表与统计跟着变，撤销能把原文原样找回来；
- 导出真的落盘且含可见记录；重发真的调用上屏入口。
视觉外观不在这里断言，靠截图人工核对。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QLabel, QMessageBox, QFileDialog

from phonemic.gui.dashboard import Dashboard
from phonemic.server import history as history_mod
from phonemic.server.history import HistoryStore
from phonemic.tunnel.mode import TunnelMode
from phonemic.utils.settings_manager import SettingsManager


@pytest.fixture
def env(qtbot, tmp_path, monkeypatch):
    import phonemic.utils.settings_manager as sm_mod
    monkeypatch.setattr(sm_mod, "get_config_dir", lambda: tmp_path)
    SettingsManager._instance = None
    store = HistoryStore(str(tmp_path / "history.db"))
    monkeypatch.setattr(history_mod, "_store", store)
    monkeypatch.setattr("phonemic.gui.dashboard.get_history_store", lambda: store)
    try:
        with patch("phonemic.gui.dashboard.get_mode", return_value=TunnelMode.LAN):
            d = Dashboard("192.168.1.100", 12000)
            d._is_force_quitting = True
            qtbot.addWidget(d)
            yield d, store
    finally:
        SettingsManager._instance = None
        store.close()


def _row_texts(d):
    texts = []
    for i in range(d._history_layout.count()):
        w = d._history_layout.itemAt(i).widget()
        if w is None or w in (d._history_empty, d._history_empty_hint):
            continue
        texts.extend(l.text() for l in w.findChildren(QLabel))
    return texts


def _seed(store):
    store.add_message("把派森改成 Python")
    store.add_message("今天下班前给素材")
    old = store.add_message("昨天的旧记录")
    # 把第三条改成昨天
    with history_mod._lock:
        store._conn.execute(
            "UPDATE message_log SET created_at=? WHERE id=?",
            ((datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds"),
             old["id"]))
        store._conn.commit()


def test_search_narrows_to_matching_records(env):
    d, store = env
    _seed(store)
    d._load_history()
    assert len(_row_texts(d)) >= 3

    d._search_field.setText("Python")
    d._on_search_changed("Python")
    d._search_timer.stop()
    d._render_history()
    texts = _row_texts(d)
    assert "把派森改成 Python" in texts
    assert "今天下班前给素材" not in texts


def test_today_filter_excludes_older_records(env):
    d, store = env
    _seed(store)
    d._load_history()
    d._on_filter_clicked("today")
    texts = _row_texts(d)
    assert "今天下班前给素材" in texts
    assert "昨天的旧记录" not in texts


def test_today_stats_reflect_store(env):
    d, store = env
    _seed(store)
    d._load_history()
    assert d._st_count.text() == "2"          # 今天两条
    assert d._today_chars == len("把派森改成Python") + len("今天下班前给素材")


def test_delete_removes_row_and_undo_restores(env):
    d, store = env
    _seed(store)
    d._load_history()
    target = d._history_rows[0]
    d._delete_record(target)
    assert target["text"] not in _row_texts(d)
    assert store.stats()["total"] == 2

    d._on_toast_undo()                       # 撤销
    assert target["text"] in _row_texts(d)
    assert store.stats()["total"] == 3


def test_clear_empties_and_undo_restores_all(env, monkeypatch):
    d, store = env
    _seed(store)
    d._load_history()
    sentinel = object()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "addButton", lambda self, *a, **k: sentinel)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: sentinel)

    d._clear_history()
    assert store.stats()["total"] == 0
    assert d._history_empty.isVisibleTo(d)

    d._on_toast_undo()
    assert store.stats()["total"] == 3
    assert "把派森改成 Python" in _row_texts(d)


def test_export_writes_visible_records(env, monkeypatch, tmp_path):
    d, store = env
    _seed(store)
    d._load_history()
    out = tmp_path / "export.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "txt")))
    d._export_history()
    content = out.read_text(encoding="utf-8")
    assert "把派森改成 Python" in content
    assert "昨天的旧记录" in content


def test_export_empty_shows_warning_and_no_file(env, monkeypatch, tmp_path):
    d, store = env
    d._load_history()
    called = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: called.append(1) or ("", "")))
    d._export_history()
    assert called == []                      # 空列表不弹保存框
    assert d._toast.isVisibleTo(d)
    assert d._toast_text.text() == d.i18n.tr("dashboard.toast_export_empty")


def test_resend_calls_flash_insert(env, monkeypatch):
    d, store = env
    rec = store.add_message("重新上屏这句话")
    d._load_history()
    seen = []
    monkeypatch.setattr("phonemic.gui.dashboard.flash_insert", lambda t: seen.append(t))
    d._resend_record(rec)
    assert seen == ["重新上屏这句话"]


def test_copy_record_puts_text_on_clipboard(env):
    d, store = env
    rec = store.add_message("复制这句话")
    d._load_history()
    d._copy_record(rec)
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == "复制这句话"