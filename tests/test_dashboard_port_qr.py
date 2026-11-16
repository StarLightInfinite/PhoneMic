"""
二维码完整性 + 端口切换 的行为测试。

二维码那组的判据来自一个真实缺陷：左列最小高度超过窗口给的空间时，Qt 布局
会把控件压到最小尺寸以下，固定尺寸的二维码 QLabel 被压扁、pixmap 居中裁切，
用户扫到缺角的码。所以断言的是「画出来的码完整」，不是某个间距数值。

端口那组守的是：主界面能改监听端口、改完二维码/地址跟着变、占用/失败时
服务不受影响且界面恢复原状、偏好持久化后下次启动生效。
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtCore import Qt

from phonemic.gui.dashboard import Dashboard, QR_SIZE
from phonemic.tunnel.mode import TunnelMode
from phonemic.utils.settings_manager import SettingsManager


@pytest.fixture
def dashboard(qtbot, tmp_path, monkeypatch):
    import phonemic.utils.settings_manager as sm_mod
    monkeypatch.setattr(sm_mod, "get_config_dir", lambda: tmp_path)
    SettingsManager._instance = None
    try:
        with patch("phonemic.gui.dashboard.get_mode", return_value=TunnelMode.LAN):
            d = Dashboard("192.168.1.100", 12000)
            d._is_force_quitting = True
            qtbot.addWidget(d)
            yield d
    finally:
        SettingsManager._instance = None


@pytest.fixture
def shown(dashboard, qtbot):
    with qtbot.waitExposed(dashboard):
        dashboard.show()
    qtbot.wait(50)
    return dashboard


def _qr_fully_drawn(d) -> bool:
    """二维码 pixmap 完整落在 QLabel 内，且 QLabel 完整落在窗口内容区里。"""
    label = d.qr_label
    pix = label.pixmap()
    if pix is None or pix.isNull():
        return False
    logical = pix.deviceIndependentSize()
    if label.width() < logical.width() or label.height() < logical.height():
        return False
    top = label.mapTo(d.centralWidget(), label.rect().topLeft()).y()
    bottom = label.mapTo(d.centralWidget(), label.rect().bottomLeft()).y()
    return top >= 0 and bottom <= d.centralWidget().height()


class TestQrNeverClipped:
    @pytest.mark.parametrize("w,h", [(1020, 700), (920, 620), (1024, 728)])
    def test_qr_intact_at_every_allowed_window_size(self, shown, w, h):
        """默认尺寸与最小尺寸下，二维码都必须完整可见（缺陷回归判据）。"""
        shown.resize(w, h)
        shown.repaint()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert _qr_fully_drawn(shown), (
            f"{w}x{h}: label={shown.qr_label.size()}, "
            f"pixmap={shown.qr_label.pixmap().deviceIndependentSize()}")

    def test_left_column_never_squeezed_below_minimum(self, shown):
        """窗口再矮，左列也只能滚动、不能被压扁——压扁就会裁到二维码。"""
        shown.resize(920, 620)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        left = shown.qr_label.parentWidget().parentWidget()
        assert left.height() >= left.minimumSizeHint().height()

    def test_qr_intact_while_approval_panel_shown(self, shown):
        """审批面板（3 条满配）挤进来时二维码仍完整。"""
        shown.resize(920, 620)
        shown.show_approval_snapshot([
            {"id": f"r{i}", "pin": p, "ip": f"10.0.0.{i}", "remaining": 30}
            for i, p in enumerate(("3847", "1234", "5678"))
        ])
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert _qr_fully_drawn(shown)

    def test_default_window_shows_all_left_controls_without_scrolling(self, shown):
        """默认 700 高度下左列一屏放全：底部工具入口不需要滚动就能够到。"""
        shown.resize(1020, 700)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        last_link = shown._tool_links["dashboard.menu_about"]
        bottom = last_link.mapTo(shown.centralWidget(), last_link.rect().bottomLeft()).y()
        assert bottom <= shown.centralWidget().height()

    def test_pixmap_never_larger_than_label(self):
        """生成的 pixmap 逻辑尺寸 ≤ 显示区：大于就会被居中裁切。"""
        from phonemic.gui.dashboard import make_qr_pixmap
        for url in ("http://192.168.1.100:12000",
                    "http://10.0.0.8:12000/example-secret-path#pk=" + "A" * 88,
                    "https://some-long-subdomain.trycloudflare.com/secret-path#pk=" + "B" * 88):
            pix = make_qr_pixmap(url, QR_SIZE)
            assert pix.deviceIndependentSize().width() <= QR_SIZE
            assert pix.deviceIndependentSize().height() <= QR_SIZE


class TestPortSwitchUi:
    def test_port_control_visible_with_current_port(self, shown):
        assert shown.port_spin.isVisibleTo(shown)
        assert shown.port_spin.value() == 12000
        assert shown.port_spin.minimum() == 1024
        assert shown.port_spin.maximum() == 65535
        assert shown.port_apply_btn.isVisibleTo(shown)

    def test_apply_same_port_is_noop(self, dashboard):
        cb = MagicMock()
        dashboard.set_port_change_callback(cb)
        dashboard._on_port_apply()
        cb.assert_not_called()
        assert dashboard._switching is False

    def test_apply_new_port_calls_callback_and_goes_busy(self, dashboard):
        cb = MagicMock()
        dashboard.set_port_change_callback(cb)
        dashboard.port_spin.setValue(13000)
        dashboard._on_port_apply()
        cb.assert_called_once_with(13000)
        assert dashboard._switching is True
        assert dashboard.port_spin.isEnabled() is False
        assert dashboard.port_apply_btn.isEnabled() is False

    def test_second_apply_while_busy_is_ignored(self, dashboard):
        cb = MagicMock()
        dashboard.set_port_change_callback(cb)
        dashboard.port_spin.setValue(13000)
        dashboard._on_port_apply()
        dashboard.port_spin.setValue(14000)
        dashboard._on_port_apply()
        cb.assert_called_once_with(13000)

    def test_failure_restores_port_and_unbusy(self, dashboard):
        dashboard.set_port_change_callback(lambda p: None)
        dashboard.port_spin.setValue(13000)
        dashboard._on_port_apply()
        dashboard.on_port_change_failed(12000)
        assert dashboard.port_spin.value() == 12000
        assert dashboard._switching is False
        assert dashboard.port_spin.isEnabled() is True
        assert "192.168.1.100:12000" in dashboard.ip_label.text()

    def test_update_network_syncs_port_spin(self, dashboard):
        dashboard.update_network("192.168.1.100", 15000)
        assert dashboard.port_spin.value() == 15000
        assert "192.168.1.100:15000" in dashboard.ip_label.text()

    def test_apply_without_callback_reverts_spin(self, dashboard):
        """回调还没接上（极早期）：不把玩不了的端口号留在界面上。"""
        dashboard.port_spin.setValue(13000)
        dashboard._on_port_apply()
        assert dashboard.port_spin.value() == 12000
        assert dashboard._switching is False

    def test_successful_switch_refreshes_qr_url(self, dashboard):
        """后端切换成功 → update_network + on_switch_completed：地址与码都换新。"""
        dashboard.set_port_change_callback(lambda p: None)
        dashboard.port_spin.setValue(13000)
        dashboard._on_port_apply()
        dashboard.update_network("192.168.1.100", 13000)
        dashboard.on_switch_completed()
        assert "192.168.1.100:13000" in dashboard.ip_label.text()
        assert dashboard._switching is False
        assert dashboard.copy_btn.isEnabled() is True


class TestServerPortPersistence:
    @pytest.fixture
    def isolated_sm(self, tmp_path, monkeypatch):
        import phonemic.utils.settings_manager as sm_mod
        monkeypatch.setattr(sm_mod, "get_config_dir", lambda: tmp_path)
        SettingsManager._instance = None
        yield sm_mod.SettingsManager.instance()
        SettingsManager._instance = None

    def test_default_port_is_12000(self, isolated_sm):
        assert isolated_sm.get("server_port") == 12000

    def test_valid_port_survives_reload(self, isolated_sm, tmp_path):
        isolated_sm.set("server_port", 13500)
        SettingsManager._instance = None
        import phonemic.utils.settings_manager as sm_mod
        assert sm_mod.SettingsManager.instance().get("server_port") == 13500

    @pytest.mark.parametrize("bad", ["12000", 80, 70000, True, None, 12.5])
    def test_invalid_port_falls_back_to_default(self, isolated_sm, tmp_path, bad):
        path = tmp_path / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["server_port"] = bad
        path.write_text(json.dumps(data), encoding="utf-8")
        SettingsManager._instance = None
        import phonemic.utils.settings_manager as sm_mod
        assert sm_mod.SettingsManager.instance().get("server_port") == 12000


class TestTunnelManagerPort:
    def test_set_port_used_on_lan_restart(self):
        from phonemic.tunnel.manager import TunnelManager

        class SyncThread:
            def __init__(self, target=None, daemon=False, **kw):
                self._target = target

            def start(self):
                self._target()

        mgr = TunnelManager(port=12000, bridge=MagicMock(), lan_ip="192.168.1.100")
        mgr.set_port(13000)
        with patch("phonemic.tunnel.manager.restart_server") as mock_restart, \
             patch("phonemic.tunnel.manager.threading.Thread", SyncThread):
            mgr.restart_service()
        mock_restart.assert_called_once_with("192.168.1.100", 13000, mgr._bridge)