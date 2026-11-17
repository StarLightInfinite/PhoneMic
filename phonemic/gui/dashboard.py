from typing import Callable, Optional
import logging
import math
import time
from datetime import datetime

import qrcode
from PySide6.QtCore import Qt, QTimer, QRect, QSize, QPoint
from PySide6.QtGui import (
    QPixmap, QAction, QActionGroup, QPainter, QColor, QFont, QLinearGradient,
    QBrush, QPen,
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QMessageBox, QLayout, QScrollArea, QButtonGroup,
    QApplication, QDialog, QRadioButton, QCheckBox, QDialogButtonBox,
    QSpinBox, QFileDialog, QSizePolicy
)

from phonemic.gui.settings_dialog import SettingsDialog
from phonemic.gui.commands_dialog import CommandsDialog
from phonemic.gui.dictionary_dialog import DictionaryDialog
from phonemic.gui.about_dialog import AboutDialog
from phonemic.gui.help_window import HelpWindow
from phonemic.gui.keyboard import flash_insert
from phonemic.tunnel.mode import TunnelMode, get_mode, set_mode, effective_auth_method
from phonemic.server.history import get_history_store
from phonemic.utils.debug import is_debug_mode
from phonemic.utils.i18n import I18n
from phonemic.utils.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


# 并发到几条就该提醒用户「可能有风险」。3 是这么定的：正常用法下最多出现 2 条
# （手机重连 + 旧页面残留），同 IP 的新连接还会把旧的那条顶掉，所以 3 条意味着
# 至少来自两个不同来源——这时才值得把注意力从"核对识别码"升到"改用扫码认证"。
APPROVAL_RISK_THRESHOLD = 3

# 二维码显示区边长（逻辑像素）。生成与占位共用同一个数，保证 pixmap 永远
# 不大于 QLabel——大于就会被居中裁切，扫出来的码缺角。
QR_SIZE = 180


def make_qr_pixmap(data: str, size: int = 250) -> QPixmap:
    """直接从 qrcode 矩阵生成 QPixmap，不依赖 PIL/Pillow。

    以 2× 设备像素绘制（setDevicePixelRatio(2)）：高 DPI 屏幕上模块边缘保持锐利，
    不再靠 SmoothTransformation 放大整张图（那会把黑白格磨成灰边，扫码识别率下降）。
    """
    qr = qrcode.QRCode(box_size=1, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    matrix_size = len(matrix)
    dpr = 2
    scale = max(1, (size * dpr) // matrix_size)
    actual_size = matrix_size * scale

    pixmap = QPixmap(actual_size, actual_size)
    pixmap.fill(Qt.white)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setBrush(QColor(0, 0, 0))
    painter.setPen(Qt.NoPen)
    for y, row in enumerate(matrix):
        for x, is_dark in enumerate(row):
            if is_dark:
                painter.drawRect(x * scale, y * scale, scale, scale)
    painter.end()

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


# ---------------------------------------------------------------------------
# 设计令牌（与定稿原型 prototype/pc-redesign.html 同源）：蓝=主色/选中，
# 紫=语音波形，绿=已连接/成功，琥珀=警示/公网，红=危险。中性色分背景/面板/描边。
# ---------------------------------------------------------------------------
_BLUE = "#1B66FF"
_PURPLE = "#7C3AED"
_GREEN = "#0FA968"
_AMBER = "#F59E0B"
_RED = "#E11D48"
_BG = "#F6F7F9"
_PANEL = "#FFFFFF"
_LINE = "#E5E8EE"
_INK = "#17233B"
_SUB = "#5B6B85"
_THUMB = "#C9D4E4"

_HEAD_FONT = '"Space Grotesk","Segoe UI","Microsoft YaHei",sans-serif'
_BODY_FONT = '"Inter","Segoe UI","Microsoft YaHei",sans-serif'
_MONO_FONT = '"JetBrains Mono","Consolas","SF Mono",monospace'

_DASHBOARD_QSS = f"""
QWidget#centralArea {{ background: {_BG}; }}
QFrame#topBar {{ background: {_PANEL}; border: none; border-bottom: 1px solid {_LINE}; }}
QLabel#appTitle {{ color: {_INK}; font-size: 17px; font-weight: 700; font-family: {_HEAD_FONT}; }}
QLabel#appSub {{ color: {_SUB}; font-size: 11px; font-family: {_BODY_FONT}; }}
QLabel#statusChip {{
    color: {_SUB}; background: {_BG}; border: 1px solid {_LINE};
    border-radius: 13px; padding: 5px 12px; font-size: 12px; font-family: {_BODY_FONT};
}}
QFrame#card {{ background: {_PANEL}; border: 1px solid {_LINE}; border-radius: 10px; }}
QLabel#cardTitle {{
    color: {_SUB}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;
    border: none; background: transparent; font-family: {_BODY_FONT};
}}
QLabel#controlLabel {{ color: {_SUB}; font-size: 11px; font-weight: 600; font-family: {_BODY_FONT}; }}
QLabel#setNote {{ color: {_SUB}; font-size: 11px; border: none; background: transparent; font-family: {_BODY_FONT}; }}
QLabel#infoNote {{ color: {_SUB}; font-size: 10px; border: none; background: transparent; font-family: {_BODY_FONT}; }}
QLabel#safetyNote {{
    color: #8A5A00; background: #FFF6E5; border: 1px solid #FCE7B8;
    border-radius: 8px; padding: 6px 8px; font-size: 10px; font-family: {_BODY_FONT};
}}
QLineEdit#urlField {{
    background: {_BG}; border: 1px solid {_LINE}; border-radius: 8px; padding: 0 9px;
    color: {_INK}; font-size: 11px; font-family: {_MONO_FONT};
}}
QLineEdit#urlField:focus {{ background: #EDF3FF; border-color: {_BLUE}; }}
QLineEdit#searchField {{
    background: {_BG}; border: 1px solid {_LINE}; border-radius: 8px; padding: 0 10px;
    color: {_INK}; font-size: 12px; font-family: {_BODY_FONT};
}}
QLineEdit#searchField:focus {{ background: #FFFFFF; border-color: {_BLUE}; }}
QPushButton#copyBtn {{
    background: {_BLUE}; color: #FFFFFF; border: none; border-radius: 8px;
    padding: 0 12px; font-size: 12px; font-weight: 600; font-family: {_BODY_FONT};
}}
QPushButton#copyBtn:hover {{ background: #1557E0; }}
QPushButton#copyBtn:pressed {{ background: #124CC4; }}
QPushButton#copyBtn:disabled {{ background: #E6E8EC; color: #A2A7B2; }}
QPushButton#miniBtn {{
    background: {_BG}; color: {_SUB}; border: 1px solid {_LINE}; border-radius: 8px;
    padding: 4px 10px; font-size: 11px; font-family: {_BODY_FONT};
}}
QPushButton#miniBtn:hover {{ background: #EDF1F7; color: {_INK}; }}
QPushButton#miniBtn:disabled {{ color: #B4B8C1; }}
QPushButton#dangerGhost {{
    background: transparent; color: {_RED}; border: 1px solid #F6C9D2; border-radius: 8px;
    padding: 4px 10px; font-size: 11px; font-family: {_BODY_FONT};
}}
QPushButton#dangerGhost:hover {{ background: #FDEEF1; }}
QPushButton[seg="true"] {{
    background: {_BG}; color: {_SUB}; border: 1px solid {_LINE}; border-radius: 8px;
    padding: 5px 8px; font-size: 12px; font-family: {_BODY_FONT};
}}
QPushButton[seg="true"]:hover {{ background: #EDF1F7; }}
QPushButton[seg="true"]:checked {{ background: {_BLUE}; color: #FFFFFF; border-color: {_BLUE}; font-weight: 600; }}
QPushButton[seg="true"]:disabled {{ color: #B4B8C1; background: #F5F6F8; }}
QPushButton#navBtn {{
    color: {_SUB}; background: transparent; border: none; border-radius: 8px;
    padding: 7px 10px; font-size: 12px; text-align: left; font-family: {_BODY_FONT};
}}
QPushButton#navBtn:hover {{ background: {_BG}; color: {_BLUE}; }}
QPushButton#rowAct {{
    color: {_SUB}; background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 1px 6px; font-size: 11px; font-family: {_BODY_FONT};
}}
QPushButton#rowAct:hover {{ background: {_BG}; color: {_BLUE}; border-color: {_LINE}; }}
QPushButton#rowAct[del="true"]:hover {{ background: #FDEEF1; color: {_RED}; border-color: #F6C9D2; }}
QLabel#deviceName {{ color: {_INK}; font-size: 15px; font-weight: 600; font-family: {_BODY_FONT}; }}
QLabel#deviceMeta, QLabel#deviceTotal, QLabel#deviceDur {{ color: {_SUB}; font-size: 11px; font-family: {_BODY_FONT}; }}
QLabel#deviceDur {{ font-family: {_MONO_FONT}; }}
QLabel#countBadge {{
    color: #0B7A4B; background: #E8F7EE; border-radius: 9px; padding: 2px 8px;
    font-size: 11px; font-weight: 600; font-family: {_BODY_FONT};
}}
QLabel#countBadge[online="false"] {{ color: {_SUB}; background: {_BG}; }}
QLabel#histDevRow {{ color: {_SUB}; font-size: 11px; font-family: {_BODY_FONT}; }}
QPushButton#histDevToggle {{
    color: {_SUB}; background: transparent; border: none; padding: 4px 0;
    font-size: 11px; text-align: left; font-family: {_BODY_FONT};
}}
QPushButton#histDevToggle:hover {{ color: {_BLUE}; }}
QLabel#statNum {{ color: {_INK}; font-size: 22px; font-weight: 700; font-family: {_HEAD_FONT}; }}
QLabel#statUnit {{ color: {_SUB}; font-size: 11px; font-family: {_BODY_FONT}; }}
QLabel#statLab {{ color: {_SUB}; font-size: 11px; font-family: {_BODY_FONT}; }}
QFrame#statSep {{ background: {_LINE}; border: none; }}
QLabel#waveTitle {{ color: {_SUB}; font-size: 11px; font-weight: 600; font-family: {_BODY_FONT}; }}
QLabel#waveStatus {{
    color: {_SUB}; background: {_BG}; border-radius: 9px; padding: 2px 8px;
    font-size: 10px; font-family: {_BODY_FONT};
}}
QLabel#waveStatus[live="true"] {{ color: #5B21B6; background: #F1EBFE; }}
QFrame#waveBox {{ background: {_BG}; border: 1px solid {_LINE}; border-radius: 8px; }}
QLabel#historyTime {{ color: {_SUB}; font-size: 11px; font-family: {_MONO_FONT}; }}
QLabel#historyChars {{
    color: {_SUB}; background: {_BG}; border-radius: 8px; padding: 1px 6px;
    font-size: 10px; font-family: {_BODY_FONT};
}}
QLabel#historyText {{ color: {_INK}; font-size: 13px; font-family: {_BODY_FONT}; }}
QFrame#historyRow {{ background: {_PANEL}; border: 1px solid {_LINE}; border-radius: 8px; }}
QFrame#historyRow[fresh="true"] {{ border: 1px solid {_BLUE}; background: #F5F8FF; }}
QLabel#historyEmpty {{ color: {_INK}; font-size: 14px; font-weight: 600; font-family: {_BODY_FONT}; }}
QLabel#historyEmptyHint {{ color: {_SUB}; font-size: 12px; font-family: {_BODY_FONT}; }}
QScrollArea#historyScroll, QScrollArea#leftScroll, QScrollArea#rightScroll {{ border: none; background: transparent; }}
QWidget#leftCol, QWidget#rightCol, QWidget#historyHost {{ background: transparent; }}
QScrollArea QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollArea QScrollBar::handle:vertical {{ background: {_THUMB}; border-radius: 4px; min-height: 28px; }}
QScrollArea QScrollBar::handle:vertical:hover {{ background: {_BLUE}; }}
QScrollArea QScrollBar::add-line:vertical, QScrollArea QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollArea QScrollBar::add-page:vertical, QScrollArea QScrollBar::sub-page:vertical {{ background: transparent; }}
QSpinBox#portSpin {{
    background: {_BG}; border: 1px solid {_LINE}; border-radius: 8px; padding: 0 8px;
    color: {_INK}; font-size: 12px; font-family: {_MONO_FONT};
}}
QSpinBox#portSpin:focus {{ background: #EDF3FF; border-color: {_BLUE}; }}
QSpinBox#portSpin:disabled {{ color: #B4B8C1; background: #F5F6F8; }}
QFrame#toast {{ background: {_INK}; border: none; border-radius: 10px; }}
QLabel#toastText {{ color: #FFFFFF; font-size: 12px; background: transparent; border: none; font-family: {_BODY_FONT}; }}
QPushButton#toastUndo {{
    color: #9DBBFF; background: transparent; border: none; font-size: 12px;
    font-weight: 700; padding: 2px 6px; font-family: {_BODY_FONT};
}}
QPushButton#toastUndo:hover {{ color: #FFFFFF; }}
"""


class _BrandMark(QWidget):
    """顶栏品牌标记：蓝→紫渐变圆角方块 + 白色麦克风 glyph。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, 30, 30)
        grad.setColorAt(0, QColor(_BLUE))
        grad.setColorAt(1, QColor(_PURPLE))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, 30, 30, 8, 8)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(12, 7, 6, 11, 3, 3)          # 麦克风头
        p.setPen(QPen(QColor("#FFFFFF"), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawArc(9, 12, 12, 10, 0, -180 * 16)          # 拾音弧
        p.drawLine(15, 22, 15, 25)                      # 支架
        p.drawLine(11, 25, 19, 25)
        p.end()


class _QrCard(QFrame):
    """二维码卡片：在二维码四角画蓝色取景框（原型同款），其余交给 QSS。

    qr_label 仍是本卡片的直接子控件——左列「不被压扁」的契约靠
    qr_label.parentWidget().parentWidget() == leftCol 这条父链成立。
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        qr = getattr(self, "_qr_label", None)
        if qr is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(_BLUE), 2.4))
        p.setBrush(Qt.NoBrush)
        g = qr.geometry()
        pad, arm = 5, 16
        x0, y0 = g.x() - pad, g.y() - pad
        x1, y1 = g.right() + pad, g.bottom() + pad
        for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                               (x0, y1, 1, -1), (x1, y1, -1, -1)):
            p.drawLine(cx, cy, cx + dx * arm, cy)
            p.drawLine(cx, cy, cx, cy + dy * arm)
        p.end()


class _Waveform(QWidget):
    """实时语音波形：40 根蓝→紫渐变条。

    只在已连接时跑动画定时器（断开即停，避免后台空转）；收到一条文本时
    burst() 注入一次能量尖峰，让「收到文字」这件事在波形上看得见。
    """

    BARS = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._active = False
        self._energy = 0.0
        self._phase = 0.0
        self._heights = [0.12] * self.BARS
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool):
        self._active = bool(active)
        if self._active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._heights = [0.12] * self.BARS
            self.update()

    def burst(self):
        self._energy = 1.0
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        self._phase += 0.35
        self._energy *= 0.86
        if not self._active and self._energy < 0.02:
            self._timer.stop()
        for i in range(self.BARS):
            if self._active:
                base = 0.30 + 0.26 * math.sin(self._phase + i * 0.55)
                base += 0.18 * math.sin(self._phase * 1.7 + i * 0.9)
                target = max(0.08, min(1.0, base + self._energy * 0.5))
            else:
                target = 0.10 + self._energy * 0.6 * math.sin(i * 0.7 + self._phase)
                target = max(0.08, target)
            self._heights[i] += (target - self._heights[i]) * 0.45
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n = self.BARS
        gap = 3
        bw = max(2.0, (w - gap * (n - 1) - 12) / n)
        mid = h / 2
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0, QColor(_BLUE))
        grad.setColorAt(1, QColor(_PURPLE))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        x = 6.0
        for i in range(n):
            bh = max(3.0, self._heights[i] * (h - 12))
            p.drawRoundedRect(QRect(int(x), int(mid - bh / 2), int(bw), int(bh)), 2, 2)
            x += bw + gap
        p.end()


class Dashboard(QMainWindow):
    # ===== 改动：构造函数增加 tray 参数（可选）=====
    def __init__(self, ip: str, port: int, tray=None, parent=None):
        super().__init__(parent)
        self.i18n = I18n.instance()
        self.sm = SettingsManager.instance()
        self.tray = tray  # 保存托盘对象引用
        self.setWindowTitle(self.i18n.tr("dashboard.title"))
        # 主界面同时承担「连接」与「历史」两块内容，固定 400×520 已经不够用；
        # 屏幕空间充足时一次把信息铺开，不再把功能藏进下拉菜单。
        # 默认高度保持 700：左列最小高度已收紧到一屏放全；更矮的窗口由左列
        # 滚动区兜底（见 _setup_ui），任何控件都不会被压扁、二维码不会被裁。
        self.resize(1020, 700)
        self.setMinimumSize(920, 620)
        self._mode: TunnelMode = get_mode()
        self._tunnel_url: Optional[str] = None
        self._qr_url: Optional[str] = f"http://{ip}:{port}"
        self._lan_ip = ip
        self._lan_port = port
        self._switching = False
        self._mode_switch_callback: Optional[Callable[[TunnelMode], None]] = None
        self._restart_service_callback: Optional[Callable[[], None]] = None
        self._port_change_callback: Optional[Callable[[int], None]] = None
        self._secure_channel = None  # SecureChannel 引用，由外部设置
        self._auth_method: str = self.sm.get("auth_method", "tofu")
        self._negotiated_algo: Optional[str] = None  # 本次连接握手协商出的算法，由 connect 事件携带
        self._algorithm_change_callback: Optional[Callable[[str], None]] = None
        # TOFU 审批：界面侧只保存"最近一次快照"这一份不可变投影，自己不维护队列
        # 状态——漏事件、乱序重绘都能靠下一次全量快照自愈（与 mobile.html 的
        # SignalHub 同哲学）。按钮作用的那条请求 = _approval_items[0]。
        self._approval_items: list = []
        self._approval_callback: Optional[Callable[[Optional[str], bool], None]] = None
        # 倒计时的两个入参：快照到达时的剩余秒数（服务端给的），与本地 monotonic
        # 锚点。界面**不持有 deadline**——超时的权威在服务端，这里只把"还剩多久"
        # 画出来，快照给的也是相对时长而非绝对时刻（绝对时刻跨进程就没意义了）。
        self._approval_remaining: Optional[int] = None
        self._approval_anchor: float = 0.0
        # connection 状态栏的两个入参：connected 此前只在 update_connection_status()
        # 里赋值（构造函数没给初值），任何在首次调用前读取 self.connected 的路径
        # 都会 AttributeError；这里补上 false，让重绘入口可以无条件自刷新。
        self.connected = False
        # 隧道公网入口可达性（保活探测结果，见 TunnelKeepalive）。True＝可达/未知。
        self._tunnel_reachable: bool = True
        # 帮助窗口单例。惰性创建：绝大多数会话不会点「使用帮助」，没必要在启动时
        # 解析一遍说明书。
        self._help_window: Optional[HelpWindow] = None
        # 设备卡片：当前连接快照（IP/UA/算法/开始时刻）+ 历史设备数
        self._device_info: dict = {}
        self._device_since: Optional[datetime] = None
        self._device_timer = QTimer(self)
        self._device_timer.setInterval(1000)
        self._device_timer.timeout.connect(self._tick_device_duration)
        # 历史列表：最新在前，最多保留 200 行控件（数据仍全在 SQLite 里）
        self._history_rows: list = []
        self._history_total = 0
        self._history_chars = 0
        # 输入流工具条状态：搜索词 + 筛选（全部/今天）+ 今日统计
        self._search_query = ""
        self._filter_mode = "all"
        self._today_count = 0
        self._today_chars = 0
        self._today_rate = 0.0
        self._toast_undo_cb = None
        self._setup_ui(ip, port)
        self._setup_menu()
        self._apply_mode_ui()
        # 上屏方式可能被偏好设置 / 托盘菜单改动，同步本菜单勾选状态
        self.sm.connect_changed("text_input_mode", self._on_input_mode_setting_changed)
        self._load_history()

    def set_restart_network_callback(self, callback):
        """设置切换网络的回调函数"""
        self._restart_network_callback = callback

    def set_mode_switch_callback(self, callback: Callable[[TunnelMode], None]):
        """设置模式切换回调函数"""
        self._mode_switch_callback = callback

    def set_restart_service_callback(self, callback: Callable[[], None]):
        """设置「重启服务」回调函数"""
        self._restart_service_callback = callback

    def set_port_change_callback(self, callback: Callable[[int], None]):
        """设置「应用端口」回调：回调负责探测占用、重启服务、失败回滚。"""
        self._port_change_callback = callback

    def _on_port_apply(self) -> None:
        """应用新端口：与模式切换共用忙碌状态，结果由后端回调收尾。

        值没变 / 正在切换中 / 回调还没接上时都是空操作——editingFinished 与
        按钮点击可能先后各触发一次，靠这几道闸保证只发起一次切换。
        """
        if self._switching:
            return
        port = self.port_spin.value()
        if port == self._lan_port:
            return
        if self._port_change_callback is None:
            self.port_spin.setValue(self._lan_port)
            return
        self._set_busy(True)
        self._set_ip_text(self.i18n.tr("dashboard.switching"))
        self._port_change_callback(port)

    def on_port_change_failed(self, port: int) -> None:
        """端口切换失败：恢复原端口显示并解除忙碌状态。"""
        self.port_spin.setValue(int(port))
        self._set_busy(False)
        self._sync_menu_checks()
        self._apply_mode_ui()

    def set_secure_channel(self, sc):
        """设置安全通道引用，并刷新 QR 码以包含公钥。"""
        self._secure_channel = sc
        self._refresh_qr()

    def set_algorithm_change_callback(self, callback: Callable[[str], None]):
        """设置认证方式变更回调函数。"""
        self._algorithm_change_callback = callback

    def set_approval_callback(self, callback: Callable[[Optional[str], bool], None]):
        """设置 TOFU 审批结果回调。

        回调签名 ``(request_id, approved)``：``request_id`` 是**界面当前显示那条**
        请求的 id，服务端据此结算那一条——于是「用户看到的识别码」与「被结算的
        请求」必然出自同一份快照。``request_id=None`` 且 ``approved=False`` 表示
        「全部拒绝」（只有并发 ≥ APPROVAL_RISK_THRESHOLD 时界面才给这个入口）。
        """
        self._approval_callback = callback

    def show_approval_snapshot(self, items) -> None:
        """按全量快照重绘审批区（``items`` 为空即收起）。

        快照来自服务端，形如 ``[{"id","pin","ip"}, ...]``，**排队最久的在前**，
        界面只显示第一条。顺序归服务端（``ApprovalRegistry.items()``）——界面不
        重排、也不挑"最新那条"：谁排在前面是一个安全决定（队首那个数字是用户正在
        核对的那一个，不能在核对途中被换掉），两份排序就等于两份真相。
        整片重绘而不是"只替换变了的字段"：识别码是从上一条继承下来
        的残留值，而用户是照着屏幕上的数字去和手机核对的——只要漏重绘一次，
        就会出现「看到的码」与「按下去的请求」不是同一条，这种错位恰好能骗过
        用户（也正是把 4 位数字拉成 30pt 的理由，不能让它是旧值）。
        """
        items = [it for it in (items or []) if isinstance(it, dict)]
        if not items:
            self.hide_approval_request()
            return
        self._approval_items = items

        head = items[0]
        total = len(items)
        # 倒计时起点：快照给的剩余秒数 + 本地锚点，之后由 _approval_timer 每秒重画。
        # 只在快照同时带了队列与秒数时才动定时器——缺字段（旧版后端、测试构造的
        # 极简快照）就退化成"没有倒计时"，而不是显示一个凭空的 0。
        remaining = head.get("remaining")
        self._approval_remaining = remaining if isinstance(remaining, int) else None
        self._approval_anchor = time.monotonic()
        # 队列长度只体现在标题上（"认证请求（共 3 条）"）：界面永远只显示队首那条，
        # 不给"上一条/下一条"的假翻页入口——按钮作用的那条必须和显示的那条一致。
        self._refresh_approval_title()
        if self._approval_remaining is None:
            self._approval_timer.stop()
        else:
            self._approval_timer.start()
        pin = head.get("pin") or ""
        # 逐位空格分隔：隔着距离也能一眼念出，便于与手机屏幕上的数字逐一核对
        self._approval_pin_label.setText(" ".join(pin) if pin else "----")
        self._approval_info.setText(
            self.i18n.tr("dashboard.approval_detail", ip=head.get("ip") or "?"))
        self._approval_accept_btn.setText(self.i18n.tr("dashboard.approval_accept"))
        self._approval_deny_btn.setText(self.i18n.tr("dashboard.approval_deny"))

        # 并发过多：不弹框，只在面板里加一行醒目文字 + 一个「全部拒绝」。
        # 弹框会骚扰根本没在用程序的人（对用户是纯打扰），而这条信息只对"正在
        # 看界面的人"有意义；面板是常驻的，用户回来一眼能看到。
        risky = total >= APPROVAL_RISK_THRESHOLD
        self._approval_deny_all_btn.setVisible(risky)
        self._approval_risk_label.setVisible(risky)
        if risky:
            self._approval_risk_label.setText(self.i18n.tr("dashboard.approval_risk"))
            self._approval_deny_all_btn.setText(
                self.i18n.tr("dashboard.approval_deny_all"))

        self._set_main_info_visible(False)
        self._approval_frame.setVisible(True)
        self._approval_frame.update()

    def hide_approval_request(self) -> None:
        """隐藏审批通知，并把地址栏/说明区还回来。"""
        self._approval_items = []
        self._approval_timer.stop()
        self._approval_remaining = None
        self._approval_frame.setVisible(False)
        self._set_main_info_visible(True)

    def _approval_seconds_left(self) -> Optional[int]:
        """按本地锚点推算当前该显示的剩余秒数；快照没给秒数时返回 None。"""
        if self._approval_remaining is None:
            return None
        elapsed = time.monotonic() - self._approval_anchor
        return max(0, self._approval_remaining - int(elapsed))

    def _refresh_approval_title(self) -> None:
        """重画标题：队列长度 + 剩余秒数。

        倒计时并进标题行而不是新起一个控件：面板高度受主界面固定高度约束，多一行
        就多一份被静默裁掉的风险（契约见 test_approval_panel_fits_fixed_window）。
        """
        total = len(self._approval_items)
        if total > 1:
            text = self.i18n.tr("dashboard.approval_title_multi", total=total)
        else:
            text = self.i18n.tr("dashboard.approval_title")
        seconds = self._approval_seconds_left()
        if seconds is not None:
            text = "{} · {}".format(
                text, self.i18n.tr("dashboard.approval_countdown", seconds=seconds))
        self._approval_title.setText(text)

    def _tick_approval_countdown(self) -> None:
        """每秒重画倒计时；归零即停表，但**不撤面板**。

        停表只是省掉无意义的刷新；真正的收尾（超时结算 + 推空快照）在服务端。界面
        自己撤面板会造出"面板没了、服务端还在等"的错位——用户想点「允许」时按钮
        已经不在，只能重连。
        """
        if not self._approval_items:
            self._approval_timer.stop()
            return
        self._refresh_approval_title()
        if self._approval_seconds_left() == 0:
            self._approval_timer.stop()

    def _set_main_info_visible(self, visible: bool) -> None:
        """审批通知与地址栏/说明区互斥显示。

        让位时两者都藏起来（含 CF 说明），恢复时按当前模式还原应有的那一条——
        与 _apply_mode_ui 的显隐规则保持一致，但不依赖它（它会在切换中提前返回）。
        """
        self.ip_label.setVisible(visible)
        if not visible:
            self.info_label.setVisible(False)
            self.cf_info_label.setVisible(False)
            return
        is_lan = self._mode == TunnelMode.LAN
        self.info_label.setVisible(is_lan)
        self.cf_info_label.setVisible(not is_lan)

    def _resolve_approval(self, approved: bool) -> None:
        """把用户对这一条（= 当前显示的队首）的决定交给后端。

        面板的撤下**不在这里做**：服务端结算后会立刻推一份新的全量快照（通常换成
        队列里下一条，或空快照收起），界面只是快照的函数。这样"谁被批准了"永远
        由服务端说了算，界面不会出现「已经点了接受、面板没了、连接却没建立」的
        无反馈状态。
        """
        items = self._approval_items
        if not items:
            return
        cb = self._approval_callback
        if cb:
            cb(items[0].get("id"), approved)

    def _resolve_approval_all(self) -> None:
        """「全部拒绝」：id=None 表示作用于队列里所有待审批请求。"""
        cb = self._approval_callback
        if cb:
            cb(None, False)

    def set_mouse_debug_window(self, win):
        """注入独立调试窗口（临时工具），由「程序」菜单打开。"""
        self._mouse_debug_win = win

    def _open_mouse_debug(self):
        win = getattr(self, "_mouse_debug_win", None)
        if win:
            win.show()
            win.raise_()
            win.activateWindow()

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def _make_segment(self, text: str, group: QButtonGroup) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setProperty("seg", True)
        btn.setCursor(Qt.PointingHandCursor)
        group.addButton(btn)
        return btn

    def _make_link_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("navBtn")
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _make_mini(self, text: str, obj: str = "miniBtn") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(obj)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    # ---------- 界面骨架：顶栏 + 审批横幅 + 三栏工作台 ----------

    def _setup_ui(self, ip, port) -> None:
        central_widget = QWidget()
        central_widget.setObjectName("centralArea")
        self.setCentralWidget(central_widget)
        self.setStyleSheet(_DASHBOARD_QSS)

        outer = QVBoxLayout(central_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())
        outer.addWidget(self._build_approval_banner())

        body = QHBoxLayout()
        body.setContentsMargins(16, 12, 16, 14)
        body.setSpacing(12)
        body.addWidget(self._build_left(ip, port), 0)
        body.addWidget(self._build_middle(), 1)
        body.addWidget(self._build_right(port), 0)
        outer.addLayout(body, 1)

        self._build_toast(central_widget)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._render_history)

        self._render_device_info({})
        self._refresh_setting_notes()

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        row.addWidget(_BrandMark())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        app_title = QLabel("PhoneMic")
        app_title.setObjectName("appTitle")
        app_sub = QLabel(self.i18n.tr("dashboard.app_sub"))
        app_sub.setObjectName("appSub")
        title_col.addWidget(app_title)
        title_col.addWidget(app_sub)
        row.addLayout(title_col)
        row.addStretch()
        self.status_label = QLabel()
        self.status_label.setObjectName("statusChip")
        self.status_label.setTextFormat(Qt.RichText)
        self.update_connection_status(False)
        row.addWidget(self.status_label, 0, Qt.AlignVCenter)
        self.refresh_btn = self._make_mini(self.i18n.tr("dashboard.refresh_link"))
        self.refresh_btn.setToolTip(self.i18n.tr("dashboard.refresh_tooltip"))
        self.refresh_btn.clicked.connect(self._on_refresh_connection)
        row.addWidget(self.refresh_btn, 0, Qt.AlignVCenter)
        return bar

    def _build_approval_banner(self) -> QFrame:
        """TOFU 审批：顶栏下方的整宽横幅（与地址栏/说明区互斥）。

        安全语义与旧版完全一致：只显示队首、按钮结算当前显示那条、倒计时只画
        不结算、并发 ≥ 阈值才出「全部拒绝」。整宽是为了让识别码/标题/风险文字
        在任何语言包下都不被水平裁切（契约见 test_approval_panel_fits_fixed_window）。
        """
        frame = QFrame()
        frame.setObjectName("approvalBanner")
        frame.setStyleSheet(
            f"QFrame#approvalBanner {{ background: #F0FAF4; border: 1px solid #CBE7D3; "
            f"border-radius: 10px; }}")
        frame.setVisible(False)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 10, 16, 12)
        lay.setSpacing(4)
        lay.setSizeConstraint(QLayout.SetMinimumSize)

        self._approval_title = QLabel()
        self._approval_title.setAlignment(Qt.AlignCenter)
        self._approval_title.setStyleSheet(
            f"font-weight: 700; color: #0B7A4B; font-size: 13px; border: none; background: transparent; font-family: {_BODY_FONT};")
        lay.addWidget(self._approval_title)

        self._approval_pin_label = QLabel()
        self._approval_pin_label.setAlignment(Qt.AlignCenter)
        pin_font = self._approval_pin_label.font()
        pin_font.setPointSize(30)
        pin_font.setBold(True)
        pin_font.setFamily("Space Grotesk")
        self._approval_pin_label.setFont(pin_font)
        self._approval_pin_label.setStyleSheet(
            "color: #0B3D22; border: none; background: transparent;")
        lay.addWidget(self._approval_pin_label)

        self._approval_info = QLabel()
        self._approval_info.setAlignment(Qt.AlignCenter)
        self._approval_info.setWordWrap(True)
        self._approval_info.setStyleSheet(
            f"color: #4A7A5D; font-size: 12px; border: none; background: transparent; font-family: {_BODY_FONT};")
        lay.addWidget(self._approval_info)

        self._approval_risk_label = QLabel()
        self._approval_risk_label.setAlignment(Qt.AlignCenter)
        self._approval_risk_label.setStyleSheet(
            f"color: {_RED}; font-size: 11px; font-weight: bold; border: none; background: transparent; font-family: {_BODY_FONT};")
        self._approval_risk_label.setVisible(False)
        lay.addWidget(self._approval_risk_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        self._approval_accept_btn = QPushButton()
        self._approval_accept_btn.setMinimumHeight(32)
        self._approval_accept_btn.setMinimumWidth(96)
        self._approval_accept_btn.setCursor(Qt.PointingHandCursor)
        self._approval_accept_btn.setStyleSheet(
            f"QPushButton {{ background: {_GREEN}; color: white; border: none; "
            f"border-radius: 8px; font-size: 13px; font-weight: 600; font-family: {_BODY_FONT}; }}"
            f"QPushButton:hover {{ background: #0C8F57; }}")
        self._approval_accept_btn.clicked.connect(lambda: self._resolve_approval(True))
        btn_row.addWidget(self._approval_accept_btn)

        self._approval_deny_btn = QPushButton()
        self._approval_deny_btn.setMinimumHeight(32)
        self._approval_deny_btn.setMinimumWidth(96)
        self._approval_deny_btn.setCursor(Qt.PointingHandCursor)
        self._approval_deny_btn.setStyleSheet(
            f"QPushButton {{ background: {_RED}; color: white; border: none; "
            f"border-radius: 8px; font-size: 13px; font-family: {_BODY_FONT}; }}"
            f"QPushButton:hover {{ background: #C4163C; }}")
        self._approval_deny_btn.clicked.connect(lambda: self._resolve_approval(False))
        btn_row.addWidget(self._approval_deny_btn)

        self._approval_deny_all_btn = QPushButton()
        self._approval_deny_all_btn.setMinimumHeight(32)
        self._approval_deny_all_btn.setCursor(Qt.PointingHandCursor)
        self._approval_deny_all_btn.setStyleSheet(
            f"QPushButton {{ background: #9F1239; color: white; border: none; "
            f"border-radius: 8px; font-size: 12px; font-family: {_BODY_FONT}; }}"
            f"QPushButton:hover {{ background: #881337; }}")
        self._approval_deny_all_btn.setVisible(False)
        self._approval_deny_all_btn.clicked.connect(self._resolve_approval_all)
        btn_row.addWidget(self._approval_deny_all_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._approval_frame = frame
        self._approval_timer = QTimer(self)
        self._approval_timer.setInterval(1000)
        self._approval_timer.timeout.connect(self._tick_approval_countdown)
        return frame

    def _build_left(self, ip, port) -> QWidget:
        left = QWidget()
        left.setObjectName("leftCol")
        left.setMinimumWidth(300)
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ----- 连接卡片：二维码 + 地址 + 复制 -----
        card = _QrCard()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(7)
        head = QHBoxLayout()
        self._scan_title = QLabel(self.i18n.tr("dashboard.scan_title"))
        self._scan_title.setObjectName("cardTitle")
        head.addWidget(self._scan_title)
        head.addStretch()
        cl.addLayout(head)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignCenter)
        qr_label.setFixedSize(QR_SIZE, QR_SIZE)
        qr_url = f"http://{ip}:{port}"
        qr_label.setPixmap(make_qr_pixmap(qr_url, QR_SIZE))
        cl.addWidget(qr_label, 0, Qt.AlignHCenter)
        self.qr_label = qr_label
        card._qr_label = qr_label

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.ip_label = QLineEdit()
        self.ip_label.setObjectName("urlField")
        self.ip_label.setReadOnly(True)
        self.ip_label.setFixedHeight(32)
        self.ip_label.setCursorPosition(0)
        self._copy_text = qr_url
        url_row.addWidget(self.ip_label, 1)
        self.copy_btn = QPushButton(self.i18n.tr("dashboard.copy_url"))
        self.copy_btn.setObjectName("copyBtn")
        self.copy_btn.setFixedHeight(32)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_url)
        url_row.addWidget(self.copy_btn, 0)
        cl.addLayout(url_row)

        self.info_label = QLabel(self.i18n.tr("dashboard.info"))
        self.info_label.setObjectName("infoNote")
        self.info_label.setWordWrap(True)
        cl.addWidget(self.info_label)
        self.cf_info_label = QLabel(self.i18n.tr("dashboard.cf_info"))
        self.cf_info_label.setObjectName("safetyNote")
        self.cf_info_label.setWordWrap(True)
        self.cf_info_label.setVisible(False)
        cl.addWidget(self.cf_info_label)
        lay.addWidget(card)
        self._set_ip_text(qr_url)

        # ----- 设备卡片 -----
        dev_card = self._make_card()
        dl = QVBoxLayout(dev_card)
        dl.setContentsMargins(12, 10, 12, 12)
        dl.setSpacing(3)
        dev_head = QHBoxLayout()
        dev_title = QLabel(self.i18n.tr("dashboard.device_title"))
        dev_title.setObjectName("cardTitle")
        dev_head.addWidget(dev_title)
        dev_head.addStretch()
        self._device_count_label = QLabel()
        self._device_count_label.setObjectName("countBadge")
        dev_head.addWidget(self._device_count_label)
        dl.addLayout(dev_head)
        self._device_name_label = QLabel()
        self._device_name_label.setObjectName("deviceName")
        dl.addWidget(self._device_name_label)
        self._device_meta_label = QLabel()
        self._device_meta_label.setObjectName("deviceMeta")
        self._device_meta_label.setWordWrap(True)
        dl.addWidget(self._device_meta_label)
        self._device_duration_label = QLabel()
        self._device_duration_label.setObjectName("deviceDur")
        self._device_duration_label.setVisible(False)
        dl.addWidget(self._device_duration_label)
        self._device_total_label = QLabel()
        self._device_total_label.setObjectName("deviceTotal")
        dl.addWidget(self._device_total_label)
        self._hist_dev_toggle = QPushButton()
        self._hist_dev_toggle.setObjectName("histDevToggle")
        self._hist_dev_toggle.setCheckable(True)
        self._hist_dev_toggle.setCursor(Qt.PointingHandCursor)
        self._hist_dev_toggle.toggled.connect(self._toggle_history_devices)
        dl.addWidget(self._hist_dev_toggle)
        self._hist_dev_box = QWidget()
        self._hist_dev_layout = QVBoxLayout(self._hist_dev_box)
        self._hist_dev_layout.setContentsMargins(0, 2, 0, 0)
        self._hist_dev_layout.setSpacing(3)
        self._hist_dev_box.setVisible(False)
        dl.addWidget(self._hist_dev_box)
        lay.addWidget(dev_card)

        lay.addStretch()

        # ----- 底部工具入口（两行紧凑导航，默认 700 高度一屏放全）-----
        self._tool_links = {}
        nav_card = self._make_card()
        nl = QVBoxLayout(nav_card)
        nl.setContentsMargins(8, 6, 8, 6)
        nl.setSpacing(2)
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        for idx, (text_key, handler) in enumerate((
            ("dashboard.menu_action", self._open_settings),
            ("dashboard.menu_command", self._open_commands_dialog),
            ("dashboard.menu_dictionary", self._open_dictionary_dialog),
            ("dashboard.menu_help_guide", self.open_user_guide),
            ("dashboard.menu_about", self.show_about),
        )):
            btn = self._make_link_button(self.i18n.tr(text_key))
            btn.clicked.connect(handler)
            self._tool_links[text_key] = btn
            (row1 if idx < 3 else row2).addWidget(btn)
        row2.addStretch()
        nl.addLayout(row1)
        nl.addLayout(row2)
        lay.addWidget(nav_card)

        scroll = QScrollArea()
        scroll.setObjectName("leftScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(left)
        scroll.setFixedWidth(308)
        self._left_scroll = scroll
        return scroll

    def _stat_block(self, num_obj: str, lab_key: str, unit_key: Optional[str] = None) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        num_row = QHBoxLayout()
        num_row.setSpacing(3)
        num = QLabel("0")
        num.setObjectName(num_obj)
        setattr(self, num_obj, num)
        num_row.addWidget(num)
        if unit_key:
            unit = QLabel(self.i18n.tr(unit_key))
            unit.setObjectName("statUnit")
            num_row.addWidget(unit, 0, Qt.AlignBottom)
        num_row.addStretch()
        v.addLayout(num_row)
        lab = QLabel(self.i18n.tr(lab_key))
        lab.setObjectName("statLab")
        v.addWidget(lab)
        return box

    def _build_middle(self) -> QWidget:
        card = self._make_card()
        card.setMinimumWidth(240)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        hist_title = QLabel(self.i18n.tr("dashboard.history_title"))
        hist_title.setObjectName("cardTitle")
        head.addWidget(hist_title)
        head.addStretch()
        self.history_refresh_btn = self._make_mini(self.i18n.tr("dashboard.history_refresh"))
        self.history_refresh_btn.clicked.connect(self._load_history)
        head.addWidget(self.history_refresh_btn)
        lay.addLayout(head)

        # ----- 今日统计 -----
        stats = QHBoxLayout()
        stats.setSpacing(10)
        stats.addWidget(self._stat_block("_st_count", "dashboard.today_count"), 1)
        sep1 = QFrame(); sep1.setObjectName("statSep"); sep1.setFixedWidth(1)
        stats.addWidget(sep1)
        stats.addWidget(self._stat_block("_st_chars", "dashboard.today_chars"), 1)
        sep2 = QFrame(); sep2.setObjectName("statSep"); sep2.setFixedWidth(1)
        stats.addWidget(sep2)
        stats.addWidget(self._stat_block("_st_rate", "dashboard.today_rate", "dashboard.rate_unit"), 1)
        lay.addLayout(stats)

        # ----- 波形 -----
        wave_head = QHBoxLayout()
        wave_title = QLabel(self.i18n.tr("dashboard.wave_title"))
        wave_title.setObjectName("waveTitle")
        wave_head.addWidget(wave_title)
        wave_head.addStretch()
        self._wave_status = QLabel(self.i18n.tr("dashboard.wave_idle"))
        self._wave_status.setObjectName("waveStatus")
        wave_head.addWidget(self._wave_status)
        lay.addLayout(wave_head)
        wave_box = QFrame()
        wave_box.setObjectName("waveBox")
        wave_box.setFixedHeight(64)
        wb = QVBoxLayout(wave_box)
        wb.setContentsMargins(2, 2, 2, 2)
        self._wave = _Waveform()
        wb.addWidget(self._wave)
        lay.addWidget(wave_box)

        # ----- 搜索 + 筛选 -----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._search_field = QLineEdit()
        self._search_field.setObjectName("searchField")
        self._search_field.setPlaceholderText(self.i18n.tr("dashboard.search_placeholder"))
        self._search_field.setClearButtonEnabled(True)
        self._search_field.setFixedHeight(30)
        self._search_field.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_field, 1)
        self._filter_group = QButtonGroup(self)
        self._seg_filter = {}
        for val, key in (("all", "dashboard.filter_all"), ("today", "dashboard.filter_today")):
            btn = self._make_segment(self.i18n.tr(key), self._filter_group)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _=False, v=val: self._on_filter_clicked(v))
            self._seg_filter[val] = btn
            toolbar.addWidget(btn, 0)
        self._seg_filter["all"].setChecked(True)
        lay.addLayout(toolbar)

        # ----- 导出 / 清空 / 总量 -----
        action = QHBoxLayout()
        action.setSpacing(8)
        self._export_btn = self._make_mini(self.i18n.tr("dashboard.export_txt"))
        self._export_btn.clicked.connect(self._export_history)
        action.addWidget(self._export_btn)
        self._clear_btn = self._make_mini(self.i18n.tr("dashboard.clear_history"), "dangerGhost")
        self._clear_btn.clicked.connect(self._clear_history)
        action.addWidget(self._clear_btn)
        action.addStretch()
        self._history_stats_label = QLabel("")
        self._history_stats_label.setObjectName("deviceTotal")
        action.addWidget(self._history_stats_label)
        lay.addLayout(action)

        # ----- 历史流 -----
        self.history_scroll = QScrollArea()
        self.history_scroll.setObjectName("historyScroll")
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._history_host = QWidget()
        self._history_host.setObjectName("historyHost")
        self._history_layout = QVBoxLayout(self._history_host)
        self._history_layout.setContentsMargins(2, 2, 8, 2)
        self._history_layout.setSpacing(6)
        self._history_empty = QLabel(self.i18n.tr("dashboard.empty_title"))
        self._history_empty.setObjectName("historyEmpty")
        self._history_empty.setWordWrap(True)
        self._history_empty.setAlignment(Qt.AlignCenter)
        self._history_empty.setContentsMargins(0, 36, 0, 0)
        self._history_empty_hint = QLabel(self.i18n.tr("dashboard.history_empty"))
        self._history_empty_hint.setObjectName("historyEmptyHint")
        self._history_empty_hint.setWordWrap(True)
        self._history_empty_hint.setAlignment(Qt.AlignCenter)
        self._history_layout.addWidget(self._history_empty)
        self._history_layout.addWidget(self._history_empty_hint)
        self.history_scroll.setWidget(self._history_host)
        lay.addWidget(self.history_scroll, 1)
        return card

    def _build_right(self, port) -> QWidget:
        right = QWidget()
        right.setObjectName("rightCol")
        right.setMinimumWidth(300)
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = self._make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(12)

        self._seg_network = {}
        self._seg_auth = {}
        self._seg_input = {}
        self._net_group = QButtonGroup(self)
        self._auth_group = QButtonGroup(self)
        self._input_group = QButtonGroup(self)

        # 网络
        net_lab = QLabel(self.i18n.tr("dashboard.group_network"))
        net_lab.setObjectName("controlLabel")
        cl.addWidget(net_lab)
        net_row = QHBoxLayout(); net_row.setSpacing(6)
        for mode, key in ((TunnelMode.LAN, "dashboard.btn_lan"),
                          (TunnelMode.CLOUDFLARE, "dashboard.btn_cloudflare")):
            btn = self._make_segment(self.i18n.tr(key), self._net_group)
            btn.clicked.connect(lambda _=False, m=mode: self._on_mode_clicked(m))
            self._seg_network[mode] = btn
            net_row.addWidget(btn, 1)
        cl.addLayout(net_row)
        self._net_note = QLabel()
        self._net_note.setObjectName("setNote")
        self._net_note.setWordWrap(True)
        cl.addWidget(self._net_note)

        # 认证
        auth_lab = QLabel(self.i18n.tr("dashboard.group_auth"))
        auth_lab.setObjectName("controlLabel")
        cl.addWidget(auth_lab)
        auth_row = QHBoxLayout(); auth_row.setSpacing(6)
        for auth, key in (("tofu", "dashboard.auth_tofu"),
                          ("url_fragment", "dashboard.auth_url_fragment")):
            btn = self._make_segment(self.i18n.tr(key), self._auth_group)
            btn.clicked.connect(lambda _=False, a=auth: self._on_auth_method_clicked(a))
            self._seg_auth[auth] = btn
            auth_row.addWidget(btn, 1)
        cl.addLayout(auth_row)
        self._auth_note = QLabel()
        self._auth_note.setObjectName("setNote")
        self._auth_note.setWordWrap(True)
        cl.addWidget(self._auth_note)

        # 上屏
        out_lab = QLabel(self.i18n.tr("dashboard.group_input"))
        out_lab.setObjectName("controlLabel")
        cl.addWidget(out_lab)
        out_row = QHBoxLayout(); out_row.setSpacing(6)
        for mode, key in (("paste", "dashboard.input_clipboard"),
                          ("type", "dashboard.input_type"),
                          ("direct", "dashboard.input_direct")):
            btn = self._make_segment(self.i18n.tr(key), self._input_group)
            btn.clicked.connect(lambda _=False, m=mode: self._on_input_mode_clicked(m))
            self._seg_input[mode] = btn
            out_row.addWidget(btn, 1)
        cl.addLayout(out_row)
        self._out_note = QLabel()
        self._out_note.setObjectName("setNote")
        self._out_note.setWordWrap(True)
        cl.addWidget(self._out_note)

        # 端口
        port_lab = QLabel(self.i18n.tr("dashboard.group_port"))
        port_lab.setObjectName("controlLabel")
        cl.addWidget(port_lab)
        port_row = QHBoxLayout(); port_row.setSpacing(6)
        self.port_spin = QSpinBox()
        self.port_spin.setObjectName("portSpin")
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setGroupSeparatorShown(False)
        self.port_spin.setValue(int(port))
        self.port_spin.setFixedHeight(28)
        self.port_spin.setToolTip(self.i18n.tr("dashboard.port_tooltip"))
        self.port_spin.editingFinished.connect(self._on_port_apply)
        port_row.addWidget(self.port_spin, 1)
        self.port_apply_btn = self._make_mini(self.i18n.tr("dashboard.port_apply"))
        self.port_apply_btn.setFixedHeight(28)
        self.port_apply_btn.setToolTip(self.i18n.tr("dashboard.port_tooltip"))
        self.port_apply_btn.clicked.connect(self._on_port_apply)
        port_row.addWidget(self.port_apply_btn, 0)
        cl.addLayout(port_row)

        # 服务
        svc_lab = QLabel(self.i18n.tr("dashboard.group_service"))
        svc_lab.setObjectName("controlLabel")
        cl.addWidget(svc_lab)
        self.switch_network_btn = self._make_mini(self.i18n.tr("dashboard.menu_switch_network"))
        self.switch_network_btn.clicked.connect(self._on_switch_network)
        cl.addWidget(self.switch_network_btn)
        self.restart_service_btn = self._make_mini(self.i18n.tr("dashboard.menu_restart_service"), "dangerGhost")
        self.restart_service_btn.clicked.connect(self._on_restart_service)
        cl.addWidget(self.restart_service_btn)

        lay.addWidget(card)
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("rightScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(right)
        scroll.setFixedWidth(308)
        self._right_scroll = scroll
        return scroll

    def _build_toast(self, parent) -> None:
        self._toast = QFrame(parent)
        self._toast.setObjectName("toast")
        self._toast.setVisible(False)
        row = QHBoxLayout(self._toast)
        row.setContentsMargins(14, 9, 10, 9)
        row.setSpacing(8)
        self._toast_text = QLabel()
        self._toast_text.setObjectName("toastText")
        row.addWidget(self._toast_text)
        self._toast_undo_btn = QPushButton(self.i18n.tr("dashboard.undo"))
        self._toast_undo_btn.setObjectName("toastUndo")
        self._toast_undo_btn.setCursor(Qt.PointingHandCursor)
        self._toast_undo_btn.setVisible(False)
        self._toast_undo_btn.clicked.connect(self._on_toast_undo)
        row.addWidget(self._toast_undo_btn)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(6000)
        self._toast_timer.timeout.connect(self._hide_toast)

    # ---------- 设置说明（随模式/认证/上屏方式联动） ----------

    def _refresh_setting_notes(self) -> None:
        if not hasattr(self, "_net_note"):
            return
        if self._mode == TunnelMode.LAN:
            self._net_note.setText(self.i18n.tr("dashboard.net_note_lan"))
        else:
            self._net_note.setText(self.i18n.tr("dashboard.net_note_cf"))
        eff = effective_auth_method(self._auth_method, self._mode)
        self._auth_note.setText(self.i18n.tr(
            "dashboard.auth_note_tofu" if eff == "tofu" else "dashboard.auth_note_qr"))
        mode = self.sm.get("text_input_mode", "paste")
        self._out_note.setText(self.i18n.tr({
            "paste": "dashboard.out_note_paste",
            "type": "dashboard.out_note_type",
            "direct": "dashboard.out_note_direct",
        }.get(mode, "dashboard.out_note_paste")))

    # ---------- 今日统计 ----------

    def _refresh_today_stats(self) -> None:
        try:
            store = get_history_store()
            fn = getattr(store, "today_stats", None)
            if callable(fn):
                data = fn()
                self._today_count = int(data.get("count", 0) or 0)
                self._today_chars = int(data.get("chars", 0) or 0)
                self._today_rate = float(data.get("rate", 0) or 0)
            else:
                raise AttributeError
        except Exception:
            today = datetime.now().strftime("%Y-%m-%d")
            rows = [r for r in self._history_rows
                    if str(r.get("created_at") or "")[:10] == today]
            self._today_count = len(rows)
            self._today_chars = sum(int(r.get("chars") or 0) for r in rows)
            self._today_rate = 0.0
        self._render_today_stats()

    def _render_today_stats(self) -> None:
        if not hasattr(self, "_st_count"):
            return
        self._st_count.setText(str(self._today_count))
        self._st_chars.setText(str(self._today_chars))
        rate = self._today_rate
        self._st_rate.setText(str(int(rate)) if float(rate).is_integer() else f"{rate:.1f}")

    # ---------- 输入流工具条：搜索 / 筛选 / 导出 / 清空 / 单条操作 ----------

    def _on_search_changed(self, text: str) -> None:
        self._search_query = text or ""
        self._search_timer.start()

    def _on_filter_clicked(self, value: str) -> None:
        self._filter_mode = value
        self._render_history()

    def _visible_rows(self) -> list:
        rows = self._history_rows
        if self._filter_mode == "today":
            today = datetime.now().strftime("%Y-%m-%d")
            rows = [r for r in rows if str(r.get("created_at") or "")[:10] == today]
        q = self._search_query.strip().lower()
        if q:
            rows = [r for r in rows if q in str(r.get("text") or "").lower()]
        return rows

    def _export_history(self) -> None:
        rows = self._visible_rows()
        if not rows:
            self._show_toast(self.i18n.tr("dashboard.toast_export_empty"))
            return
        default = "PhoneMic-输入导出-{}.txt".format(datetime.now().strftime("%Y%m%d-%H%M"))
        path, _ = QFileDialog.getSaveFileName(
            self, self.i18n.tr("dashboard.export_txt"), default, "Text Files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("PhoneMic 输入导出 · {}\n\n".format(
                    datetime.now().strftime("%Y-%m-%d %H:%M")))
                for r in rows:
                    fh.write("[{}] {}\n".format(
                        self._format_history_time(str(r.get("created_at") or "")),
                        str(r.get("text") or "")))
        except OSError:
            logger.exception("导出历史失败")
            return
        self._show_toast(self.i18n.tr("dashboard.toast_exported", n=len(rows)))

    def _clear_history(self) -> None:
        if not self._history_rows:
            self._show_toast(self.i18n.tr("dashboard.toast_export_empty"))
            return
        n = len(self._history_rows)
        box = QMessageBox(self)
        box.setWindowTitle(self.i18n.tr("dashboard.clear_confirm_title"))
        box.setText(self.i18n.tr("dashboard.clear_confirm_body", n=n))
        box.setIcon(QMessageBox.Warning)
        ok = box.addButton(self.i18n.tr("dashboard.clear_history"), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not ok:
            return
        try:
            removed = get_history_store().clear_messages()
        except Exception:
            logger.exception("清空历史失败")
            return
        self._load_history()
        self._show_toast(
            self.i18n.tr("dashboard.toast_cleared", n=len(removed)),
            undo=lambda: self._restore_records(removed))

    def _restore_records(self, records) -> None:
        try:
            get_history_store().restore_messages(records)
        except Exception:
            logger.exception("恢复历史失败")
            return
        self._load_history()

    def _delete_record(self, record) -> None:
        rid = record.get("id")
        try:
            removed = get_history_store().delete_message(rid) if rid is not None else record
        except Exception:
            logger.exception("删除历史失败")
            removed = record
        self._history_rows = [r for r in self._history_rows if r.get("id") != rid]
        self._history_total = max(0, self._history_total - 1)
        self._history_chars = max(0, self._history_chars - int(record.get("chars") or 0))
        self._render_history()
        self._refresh_today_stats()
        self._show_toast(
            self.i18n.tr("dashboard.toast_deleted"),
            undo=lambda: self._restore_records([removed or record]))

    def _resend_record(self, record) -> None:
        text = str(record.get("text") or "")
        if not text:
            return
        try:
            flash_insert(text)
        except Exception:
            logger.exception("重新上屏失败")
            return
        self._show_toast(self.i18n.tr("dashboard.toast_resent"))

    def _copy_record(self, record) -> None:
        QApplication.clipboard().setText(str(record.get("text") or ""))
        self._show_toast(self.i18n.tr("dashboard.toast_copied"))

    # ---------- 底部提示（toast）：删除/清空后可撤销 ----------

    def _show_toast(self, text: str, undo=None) -> None:
        self._toast_text.setText(text)
        self._toast_undo_cb = undo
        self._toast_undo_btn.setVisible(callable(undo))
        self._toast.adjustSize()
        self._position_toast()
        self._toast.setVisible(True)
        self._toast.raise_()
        self._toast_timer.start()

    def _hide_toast(self) -> None:
        self._toast_timer.stop()
        self._toast.setVisible(False)
        self._toast_undo_cb = None

    def _on_toast_undo(self) -> None:
        cb = self._toast_undo_cb
        self._hide_toast()
        if callable(cb):
            cb()

    def _position_toast(self) -> None:
        cw = self.centralWidget()
        if cw is None:
            return
        self._toast.adjustSize()
        x = max(8, (cw.width() - self._toast.width()) // 2)
        y = max(8, cw.height() - self._toast.height() - 18)
        self._toast.move(x, y)

    # ---------- 历史设备（最近会话，可折叠） ----------

    def _toggle_history_devices(self, checked: bool) -> None:
        self._hist_dev_box.setVisible(checked)

    def _populate_history_devices(self, devices: int) -> None:
        if not hasattr(self, "_hist_dev_toggle"):
            return
        self._hist_dev_toggle.setText(self.i18n.tr("dashboard.history_devices", n=devices))
        while self._hist_dev_layout.count():
            item = self._hist_dev_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide(); w.setParent(None); w.deleteLater()
        try:
            sessions = get_history_store().recent_sessions(limit=5)
        except Exception:
            sessions = []
        if not sessions:
            empty = QLabel(self.i18n.tr("dashboard.no_history_devices"))
            empty.setObjectName("histDevRow")
            self._hist_dev_layout.addWidget(empty)
            return
        for s in sessions:
            kind = self._device_kind(str(s.get("user_agent") or "")) or self.i18n.tr("dashboard.device_unknown")
            ip = str(s.get("ip") or "")
            when = self._format_history_time(str(s.get("connected_at") or ""))
            lab = QLabel(" · ".join(p for p in (kind, ip, when) if p))
            lab.setObjectName("histDevRow")
            lab.setWordWrap(True)
            self._hist_dev_layout.addWidget(lab)

    def _on_refresh_connection(self) -> None:
        """「刷新」二维码与链接：按当前模式重新生成一次服务身份。

        会换新的 secret 路径与密钥对（Cloudflare 还会换新域名），旧链接立即失效、
        手机需要重扫。这正是用户点「刷新」时想要的语义：链接坏了 / 想踢掉旧设备
        时，按一下拿到一份全新的。
        """
        if self._switching:
            return
        if self._restart_service_callback is None:
            # 回调尚未接上（极早期点击）：至少把当前二维码重绘一遍
            self._refresh_qr()
            return
        self._set_busy(True)
        self._set_ip_text(self.i18n.tr("dashboard.refreshing"))
        self._reset_tunnel_reachability()
        self._restart_service_callback()

    # ---------- 设备卡片 ----------

    def _tick_device_duration(self) -> None:
        if self._device_since is None:
            self._device_timer.stop()
            return
        self._render_device_info(self._device_info)

    def update_device_info(self, payload) -> None:
        """接收服务端 device_info 快照：当前设备 + 历史设备数 + 最近会话。"""
        if not isinstance(payload, dict):
            return
        self._device_info = payload
        since = payload.get("since") or ""
        self._device_since = None
        if payload.get("connected") and since:
            try:
                self._device_since = datetime.fromisoformat(since)
            except ValueError:
                self._device_since = None
        if self._device_since is not None:
            if not self._device_timer.isActive():
                self._device_timer.start()
        else:
            self._device_timer.stop()
        self._render_device_info(payload)

    @staticmethod
    def _device_kind(user_agent: str) -> str:
        """把 UA 压成「iPhone · Safari」这种一眼能读的标签。"""
        ua = (user_agent or "").lower()
        if not ua:
            return ""
        if "iphone" in ua:
            device = "iPhone"
        elif "ipad" in ua:
            device = "iPad"
        elif "android" in ua:
            device = "Android"
        elif "windows" in ua:
            device = "Windows"
        elif "macintosh" in ua or "mac os" in ua:
            device = "Mac"
        elif "linux" in ua:
            device = "Linux"
        else:
            device = ""

        if "edg" in ua:
            browser = "Edge"
        elif "crios" in ua:
            browser = "Chrome"
        elif "firefox" in ua or "fxios" in ua:
            browser = "Firefox"
        elif "chrome" in ua:
            browser = "Chrome"
        elif "safari" in ua:
            browser = "Safari"
        else:
            browser = ""
        if device and browser:
            return f"{device} · {browser}"
        return device or browser

    def _duration_text(self) -> str:
        if self._device_since is None:
            return ""
        seconds = max(0, int(time.time() - self._device_since.timestamp()))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _render_device_info(self, payload) -> None:
        if not hasattr(self, "_device_count_label"):
            return
        info = payload or self._device_info or {}
        connected = bool(info.get("connected"))
        devices = int(info.get("devices") or 0)
        online = 1 if connected else 0

        self._device_count_label.setText(
            self.i18n.tr("dashboard.device_online", n=online) if connected
            else self.i18n.tr("dashboard.device_offline")
        )
        self._device_count_label.setProperty("online", "true" if connected else "false")
        self._device_count_label.style().unpolish(self._device_count_label)
        self._device_count_label.style().polish(self._device_count_label)

        if connected:
            kind = self._device_kind(str(info.get("user_agent") or ""))
            self._device_name_label.setText(kind or self.i18n.tr("dashboard.device_unknown"))
            meta = []
            if info.get("ip"):
                meta.append(str(info["ip"]))
            algo = str(info.get("algorithm") or "")
            if algo and algo != "none":
                meta.append(self._algo_display_name(algo))
            self._device_meta_label.setText(" · ".join(meta))
            self._device_meta_label.setVisible(True)
            duration = self._duration_text()
            if duration:
                self._device_duration_label.setText(
                    self.i18n.tr("dashboard.device_duration", duration=duration))
                self._device_duration_label.setVisible(True)
            else:
                self._device_duration_label.setVisible(False)
        else:
            self._device_name_label.setText(self.i18n.tr("dashboard.device_none"))
            self._device_meta_label.setText(self.i18n.tr("dashboard.device_hint"))
            self._device_meta_label.setVisible(True)
            self._device_duration_label.setVisible(False)

        self._device_total_label.setText(
            self.i18n.tr("dashboard.device_total", n=devices))
        self._populate_history_devices(devices)

    # ---------- 输入流（与手机端同一份 SQLite） ----------

    def _load_history(self) -> None:
        """从 SQLite 读取最近历史与统计，重绘中间输入流。"""
        try:
            store = get_history_store()
            records = store.recent_messages(limit=200)
            stats = store.stats()
        except Exception:
            logger.exception("加载输入历史失败")
            records, stats = [], {}
        self._history_rows = list(records)
        self._history_total = int(stats.get("total", len(records)) or 0)
        self._history_chars = int(stats.get("total_chars", 0) or 0)
        self._render_history()
        self._refresh_today_stats()

    def add_history_record(self, record) -> None:
        """服务端推来一条新历史：插到最前，统计 +1，波形尖峰，并重绘列表。"""
        if not isinstance(record, dict):
            return
        self._history_rows.insert(0, record)
        if len(self._history_rows) > 200:
            del self._history_rows[200:]
        self._history_total += 1
        try:
            self._history_chars += int(record.get("chars") or 0)
        except (TypeError, ValueError):
            pass
        self._fresh_id = record.get("id")
        QTimer.singleShot(1200, self._clear_fresh)
        if hasattr(self, "_wave"):
            self._wave.burst()
        self._render_history()
        self._refresh_today_stats()

    def _clear_fresh(self) -> None:
        self._fresh_id = None
        for i in range(self._history_layout.count()):
            w = self._history_layout.itemAt(i).widget()
            if w is not None and w.property("fresh"):
                w.setProperty("fresh", False)
                w.style().unpolish(w); w.style().polish(w)

    def _render_history(self) -> None:
        if not hasattr(self, "_history_layout"):
            return
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self._history_empty, self._history_empty_hint):
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        if hasattr(self, "_history_stats_label"):
            self._history_stats_label.setText(
                self.i18n.tr(
                    "dashboard.history_stats",
                    total=self._history_total,
                    chars=self._history_chars,
                )
            )

        visible = self._visible_rows()
        if not visible:
            if self._history_rows:
                self._history_empty.setText(self.i18n.tr("dashboard.empty_search_title"))
                self._history_empty_hint.setText(self.i18n.tr("dashboard.empty_search_hint"))
            else:
                self._history_empty.setText(self.i18n.tr("dashboard.empty_title"))
                self._history_empty_hint.setText(self.i18n.tr("dashboard.history_empty"))
            self._history_layout.addWidget(self._history_empty)
            self._history_layout.addWidget(self._history_empty_hint)
            self._history_empty.setVisible(True)
            self._history_empty_hint.setVisible(True)
            self._history_layout.addStretch(1)
            return

        self._history_empty.setVisible(False)
        self._history_empty_hint.setVisible(False)
        fresh_id = getattr(self, "_fresh_id", None)
        for record in visible:
            self._history_layout.addWidget(self._make_history_row(record, fresh_id))
        self._history_layout.addStretch(1)
        # 换行文本的实际高度依赖当时的面板宽度：等本帧布局完成后再逐个定高，
        # 否则外层布局会按「一行」的最小高度分配，多行内容被裁掉。
        QTimer.singleShot(0, self._fit_history_rows)

    def _make_history_row(self, record, fresh_id=None) -> QWidget:
        row = QFrame()
        row.setObjectName("historyRow")
        is_fresh = fresh_id is not None and record.get("id") == fresh_id
        row.setProperty("fresh", bool(is_fresh))
        layout = QVBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        time_label = QLabel(self._format_history_time(str(record.get("created_at") or "")))
        time_label.setObjectName("historyTime")
        head.addWidget(time_label)
        head.addStretch()
        chars_label = QLabel(self.i18n.tr("dashboard.history_chars", n=int(record.get("chars") or 0)))
        chars_label.setObjectName("historyChars")
        head.addWidget(chars_label)
        copy_btn = self._make_mini(self.i18n.tr("dashboard.copy_entry"), "rowAct")
        copy_btn.setToolTip(self.i18n.tr("dashboard.copy_entry"))
        copy_btn.clicked.connect(lambda _=False, r=record: self._copy_record(r))
        head.addWidget(copy_btn)
        resend_btn = self._make_mini(self.i18n.tr("dashboard.resend_short"), "rowAct")
        resend_btn.setToolTip(self.i18n.tr("dashboard.resend"))
        resend_btn.clicked.connect(lambda _=False, r=record: self._resend_record(r))
        head.addWidget(resend_btn)
        del_btn = self._make_mini(self.i18n.tr("dashboard.delete_entry"), "rowAct")
        del_btn.setProperty("del", True)
        del_btn.clicked.connect(lambda _=False, r=record: self._delete_record(r))
        head.addWidget(del_btn)
        layout.addLayout(head)

        text_label = QLabel(str(record.get("text") or ""))
        text_label.setObjectName("historyText")
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text_label)
        row._text_label = text_label
        row._head_h = max(18, time_label.sizeHint().height(), copy_btn.sizeHint().height())
        return row

    def _fit_history_rows(self) -> None:
        """按当前宽度计算每条历史的真实高度（含换行）。

        QVBoxLayout 在外层带 stretch 时只给子项最小高度，QLabel 的自动换行
        因此会被裁掉；这里用字体度量把「文本实际需要几行」算出来，把每行
        固定成对应高度。面板宽度变化时由 resizeEvent 重新触发。
        """
        if not hasattr(self, "_history_layout"):
            return
        row_w = max(220, self.history_scroll.viewport().width() - 12)
        text_w = row_w - 20
        for i in range(self._history_layout.count()):
            widget = self._history_layout.itemAt(i).widget()
            label = getattr(widget, "_text_label", None) if widget is not None else None
            if label is None:
                continue
            widget.setFixedWidth(row_w)
            metrics = label.fontMetrics()
            rect = metrics.boundingRect(
                QRect(0, 0, text_w, 10000), int(Qt.TextWordWrap), label.text())
            text_h = max(metrics.height(), rect.height())
            widget.setFixedHeight(16 + int(widget._head_h) + 4 + text_h)
            label.setFixedWidth(text_w)

    @staticmethod
    def _format_history_time(value: str) -> str:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        now = datetime.now().astimezone()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%m-%d %H:%M")

    def _apply_mode_ui(self) -> None:
        """根据当前模式更新菜单勾选状态和元素显隐。"""
        if self._switching:
            blank = QPixmap(QR_SIZE, QR_SIZE)
            blank.fill(Qt.transparent)
            self.qr_label.setPixmap(blank)
            return

        if self._mode == TunnelMode.LAN:
            self.info_label.setVisible(True)
            self.cf_info_label.setVisible(False)
            self.switch_network_action.setEnabled(True)
            if hasattr(self, "switch_network_btn"):
                self.switch_network_btn.setEnabled(not self._switching)
            self._refresh_qr()
        else:
            self.info_label.setVisible(False)
            self.cf_info_label.setVisible(True)
            self.switch_network_action.setEnabled(False)
            if hasattr(self, "switch_network_btn"):
                self.switch_network_btn.setEnabled(False)
            if self._tunnel_url:
                self._refresh_qr()
            else:
                self._set_ip_text(self.i18n.tr("dashboard.cf_connecting"))

        # 审批通知在显示中时把说明标签再让回去：本方法会被模式切换/重连等路径调用，
        # 不补这一手就会让说明标签与审批面板同时占着同一块位置。
        if self._approval_frame.isVisible():
            self._set_main_info_visible(False)
        self._refresh_setting_notes()

    def _sync_menu_checks(self) -> None:
        """根据当前模式同步菜单勾选状态。"""
        self.act_lan.setChecked(self._mode == TunnelMode.LAN)
        self.act_cf.setChecked(self._mode == TunnelMode.CLOUDFLARE)
        # Cloudflare 模式下禁用 TOFU（公网无信任锚，强制扫码认证）
        self.act_auth_tofu.setEnabled(self._mode == TunnelMode.LAN)
        eff = effective_auth_method(self._auth_method, self._mode)
        self.act_auth_tofu.setChecked(eff == "tofu")
        self.act_auth_url_fragment.setChecked(eff == "url_fragment")

        # 常显分段按钮与 QAction 同源：任何路径改了状态（托盘、快捷键、事件）
        # 都在这里一次同步，避免两套控件互相打架。
        seg_net = getattr(self, "_seg_network", {})
        for m, btn in seg_net.items():
            btn.setChecked(m == self._mode)
        seg_auth = getattr(self, "_seg_auth", {})
        if "tofu" in seg_auth:
            seg_auth["tofu"].setEnabled(self._mode == TunnelMode.LAN)
        for a, btn in seg_auth.items():
            btn.setChecked(a == eff)
        self._refresh_setting_notes()

    def _set_busy(self, busy: bool) -> None:
        """进入/退出「切换中·重启中」状态：期间禁止再次触发网络菜单的操作。

        切换模式与重启服务共用一个状态位——一次网络动作没结束前再点任何一个，
        都只会让服务端白重启一次，必须挡住。
        """
        self._switching = busy
        self.act_lan.setEnabled(not busy)
        self.act_cf.setEnabled(not busy)
        self.act_restart_service.setEnabled(not busy)
        for group in ("_seg_network", "_seg_auth", "_seg_input"):
            for btn in getattr(self, group, {}).values():
                btn.setEnabled(not busy)
        for name in ("refresh_btn", "restart_service_btn", "switch_network_btn",
                     "port_spin", "port_apply_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)

    def _on_mode_clicked(self, target_mode: TunnelMode) -> None:
        """点击模式切换菜单项。"""
        if target_mode == self._mode:
            return
        if self._switching:
            self._sync_menu_checks()
            return
        self._set_busy(True)
        self._set_ip_text(self.i18n.tr("dashboard.switching"))
        self._mode = target_mode
        # 换模式＝旧的保活结论作废（保活只在 Cloudflare 模式运行）
        self._reset_tunnel_reachability()
        set_mode(target_mode)
        self._apply_mode_ui()
        if self._mode_switch_callback:
            self._mode_switch_callback(target_mode)

    def _on_auth_method_clicked(self, auth_method: str) -> None:
        """点击认证方式菜单项（"tofu" 手动审批 / "url_fragment" 扫码认证）。"""
        if auth_method == self._auth_method:
            return
        # Cloudflare 模式拒绝 TOFU，公网无信任锚
        if auth_method == "tofu" and self._mode == TunnelMode.CLOUDFLARE:
            self._sync_menu_checks()
            return
        self._auth_method = auth_method
        self.sm.set("auth_method", auth_method)
        if self._algorithm_change_callback:
            self._algorithm_change_callback(auth_method)
        self._refresh_qr()
        self.update_connection_status(self.connected)
        self._sync_menu_checks()

    def _on_input_mode_clicked(self, mode: str) -> None:
        """点击上屏方式菜单项（paste 剪贴板 / type 模拟键盘 / direct 直接输入），立即持久化。"""
        if mode != self.sm.get("text_input_mode", "paste"):
            self.sm.set("text_input_mode", mode)
        self._sync_input_checks()

    def _on_input_mode_setting_changed(self, _mode) -> None:
        """配置变更回调：偏好设置 / 托盘菜单改动后同步勾选状态。"""
        self._sync_input_checks()

    def _sync_input_checks(self) -> None:
        """根据当前配置同步上屏方式菜单的勾选状态。"""
        mode = self.sm.get("text_input_mode", "paste")
        self.act_input_paste.setChecked(mode == "paste")
        self.act_input_type.setChecked(mode == "type")
        self.act_input_direct.setChecked(mode == "direct")
        for m, btn in getattr(self, "_seg_input", {}).items():
            btn.setChecked(m == mode)
        self._refresh_setting_notes()

    def on_switch_completed(self) -> None:
        """模式切换 / 重启服务结束（成功或失败），恢复菜单可用状态。"""
        self._set_busy(False)
        self._sync_menu_checks()
        self._apply_mode_ui()

    def _get_qr_url(self) -> str:
        """获取当前 QR 码 URL（含 PC 公钥 fragment）。"""
        if self._mode == TunnelMode.CLOUDFLARE and self._tunnel_url:
            url = self._tunnel_url
        else:
            url = f"http://{self._lan_ip}:{self._lan_port}"
        if self._secure_channel:
            url = self._secure_channel.append_to_url(url)
        return url

    def get_entry_url(self) -> str:
        """当前入口 URL（与二维码完全一致），供局域网发现服务使用。

        安卓壳 APP 经 UDP 发现拿到本值后直接 WebView 加载，认证路径与扫码
        完全相同（fragment 里的 PC 公钥 / secret 路径都带上了），不另起一套。
        """
        return self._get_qr_url()
    def _set_ip_text(self, text: str) -> None:
        """更新地址栏文本，并同步「复制链接」是否可用。

        用 QLineEdit 而不是富文本：URL 里的 ``&`` / ``<`` / ``>`` 不需要转义，
        光标停在开头看到的是域名而不是尾巴；只读状态下仍可选中、Ctrl+C。
        状态文案（切换中 / 隧道断开）不是 URL，此时把复制按钮置灰——
        否则用户复制到的是一句提示语。
        """
        self.ip_label.setText(text)
        self.ip_label.setToolTip(text)
        self.ip_label.setCursorPosition(0)
        copyable = text.startswith("http://") or text.startswith("https://")
        self._copy_text = text if copyable else ""
        if hasattr(self, "copy_btn"):
            self.copy_btn.setEnabled(bool(copyable))
            self.copy_btn.setText(self.i18n.tr("dashboard.copy_url"))

    def _copy_url(self) -> None:
        """复制当前连接地址（含扫码认证所需的公钥 fragment）。"""
        if not self._copy_text:
            return
        QApplication.clipboard().setText(self._copy_text)
        if hasattr(self, "copy_btn"):
            self.copy_btn.setText(self.i18n.tr("dashboard.copied"))
            QTimer.singleShot(1500, self._restore_copy_btn)

    def _restore_copy_btn(self) -> None:
        if hasattr(self, "copy_btn"):
            self.copy_btn.setText(self.i18n.tr("dashboard.copy_url"))

    def _refresh_qr(self) -> None:
        """刷新 QR 码和地址栏。"""
        if self._switching:
            return
        url = self._get_qr_url()
        self.qr_label.setPixmap(make_qr_pixmap(url, QR_SIZE))
        self._set_ip_text(url)

    def update_tunnel_url(self, url: Optional[str]) -> None:
        """更新隧道 URL（Cloudflare 模式下更新二维码和地址）。

        收到有效 URL 意味着隧道已建立，自动结束切换状态。
        """
        self._tunnel_url = url
        if self._mode == TunnelMode.CLOUDFLARE:
            if url:
                # 新域名到手＝隧道刚重建，之前的「失效」标记随之作废
                self._reset_tunnel_reachability()
                if self._switching:
                    self.on_switch_completed()
                else:
                    self._refresh_qr()
            else:
                self._set_ip_text("Cloudflare: " + self.i18n.tr("dashboard.status_disconnected"))

    def get_mode(self) -> TunnelMode:
        """返回当前模式。"""
        return self._mode

    def _setup_menu(self):
        """建立内部 QAction 与快捷键；主界面上不再有菜单栏。

        过去「程序 / 网络 / 上屏方式 / 帮助」都藏在下拉里，用户要先点开才知道有
        什么。现在这些入口全部常显（见 ``_setup_ui`` 的分段按钮与底部链接），
        本方法只保留 QAction，供托盘、快捷键与状态同步复用。
        """
        self._mode_action_group = QActionGroup(self)
        self._mode_action_group.setExclusive(True)
        self.act_lan = QAction(self.i18n.tr("dashboard.btn_lan"), self)
        self.act_lan.setCheckable(True)
        self.act_lan.triggered.connect(lambda: self._on_mode_clicked(TunnelMode.LAN))
        self._mode_action_group.addAction(self.act_lan)

        self.act_cf = QAction(self.i18n.tr("dashboard.btn_cloudflare"), self)
        self.act_cf.setCheckable(True)
        self.act_cf.triggered.connect(lambda: self._on_mode_clicked(TunnelMode.CLOUDFLARE))
        self._mode_action_group.addAction(self.act_cf)

        self._auth_action_group = QActionGroup(self)
        self._auth_action_group.setExclusive(True)
        self.act_auth_tofu = QAction(self.i18n.tr("dashboard.auth_tofu"), self)
        self.act_auth_tofu.setCheckable(True)
        self.act_auth_tofu.triggered.connect(lambda: self._on_auth_method_clicked("tofu"))
        self._auth_action_group.addAction(self.act_auth_tofu)

        self.act_auth_url_fragment = QAction(self.i18n.tr("dashboard.auth_url_fragment"), self)
        self.act_auth_url_fragment.setCheckable(True)
        self.act_auth_url_fragment.triggered.connect(
            lambda: self._on_auth_method_clicked("url_fragment"))
        self._auth_action_group.addAction(self.act_auth_url_fragment)

        self.switch_network_action = QAction(self.i18n.tr("dashboard.menu_switch_network"), self)
        self.switch_network_action.triggered.connect(self._on_switch_network)
        self.switch_network_action.setEnabled(self._mode == TunnelMode.LAN)

        self.act_restart_service = QAction(self.i18n.tr("dashboard.menu_restart_service"), self)
        self.act_restart_service.triggered.connect(self._on_restart_service)

        self._input_action_group = QActionGroup(self)
        self._input_action_group.setExclusive(True)
        self.act_input_paste = QAction(self.i18n.tr("dashboard.input_clipboard"), self)
        self.act_input_paste.setCheckable(True)
        self.act_input_paste.triggered.connect(lambda: self._on_input_mode_clicked("paste"))
        self._input_action_group.addAction(self.act_input_paste)

        self.act_input_type = QAction(self.i18n.tr("dashboard.input_type"), self)
        self.act_input_type.setCheckable(True)
        self.act_input_type.triggered.connect(lambda: self._on_input_mode_clicked("type"))
        self._input_action_group.addAction(self.act_input_type)

        self.act_input_direct = QAction(self.i18n.tr("dashboard.input_direct"), self)
        self.act_input_direct.setCheckable(True)
        self.act_input_direct.setToolTip(self.i18n.tr("settings.input_mode_tooltip"))
        self.act_input_direct.triggered.connect(lambda: self._on_input_mode_clicked("direct"))
        self._input_action_group.addAction(self.act_input_direct)

        # 键盘用户仍然可用：Ctrl+Q 退出，避免去掉菜单栏后丢失快捷入口
        self.act_exit = QAction(self.i18n.tr("dashboard.menu_exit"), self)
        self.act_exit.setShortcut("Ctrl+Q")
        self.act_exit.triggered.connect(self._quit_app)
        self.addAction(self.act_exit)

        if is_debug_mode():
            self.act_mouse_debug = QAction("鼠标曲线调试", self)
            self.act_mouse_debug.triggered.connect(self._open_mouse_debug)
            self.addAction(self.act_mouse_debug)

        self._sync_menu_checks()
        self._sync_input_checks()

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _open_commands_dialog(self):
        dlg = CommandsDialog(self)
        dlg.exec_()

    def _open_dictionary_dialog(self):
        dlg = DictionaryDialog(self)
        dlg.exec_()

    def _on_switch_network(self):
        """触发切换网络回调"""
        if self._restart_network_callback:
            self._restart_network_callback()

    def _on_restart_service(self) -> None:
        """点击「重启服务」：按当前模式把服务重来一遍。

        会换新身份（secret 路径与密钥对都变），手机端需重新配对：扫码认证重新扫
        二维码，手动审批则再点一次「允许」。
        这里先清掉 `_tunnel_url` 并擦掉二维码：否则 CF 重启失败时，界面会把
        已经作废的旧域名当成有效地址继续显示，用户扫了也连不上。
        """
        if self._switching:
            return
        self._set_busy(True)
        self._set_ip_text(self.i18n.tr("dashboard.restarting"))
        self._tunnel_url = None
        # 重启正是失效时的自救动作，清掉失效标记（拿到新 URL 后会再次刷新）
        self._reset_tunnel_reachability()
        self._apply_mode_ui()  # _switching=True → 先清空二维码，避免扫到已作废的旧码
        if self._restart_service_callback:
            self._restart_service_callback()

    def update_network(self, ip: str, port: int):
        """更新主界面的 IP、端口和二维码显示"""
        self._lan_ip = ip
        self._lan_port = port
        if hasattr(self, "port_spin"):
            self.port_spin.setValue(int(port))
        self._refresh_qr()

    def show_about(self):
        # 用自定义窗口而非 QMessageBox.about：版本号要可选中复制，链接要真的能点开
        # （理由见 about_dialog.py 顶部）。
        AboutDialog(self).exec()

    def open_user_guide(self):
        # 帮助窗口是单例：重复点菜单只把已经开着的那个提到前面，不再开第二个。
        # 非模态且不带父窗口（见 HelpWindow 的说明）：照步骤操作主界面时需要它一直在
        # 旁边，但又不能反过来压住主界面——所以它是独立顶层窗口，主界面点到就上来。
        if self._help_window is None:
            self._help_window = HelpWindow()
        if not self._help_window.show_help():
            QMessageBox.warning(self, self.i18n.tr("help.warning"),
                                self.i18n.tr("help.file_not_found"))

    def _algo_display_name(self, algo: str) -> str:
        """算法显示名：优先取 locale，缺失时回退为原始算法名。"""
        key = f"dashboard.algo_{algo}"
        tr = self.i18n.tr(key)
        return tr if tr != key else algo

    def update_connection_status(self, connected: bool, algorithm: Optional[str] = None) -> None:
        self.connected = connected
        # algorithm 仅在 connect 事件中携带；断开时清空协商结果
        if connected and algorithm is not None:
            self._negotiated_algo = algorithm
        elif not connected:
            self._negotiated_algo = None
        if connected:
            text = '<span style="color:green;">●</span> ' + self.i18n.tr("dashboard.status_connected")
            # 加密永远开启，状态栏展示认证方式 + 协商算法
            eff_auth = effective_auth_method(self._auth_method, self._mode)
            auth_display = self.i18n.tr("dashboard.status_auth_tofu" if eff_auth == "tofu" else "dashboard.status_auth_url_fragment")
            text += ' <span style="color:#666;">| ' + auth_display
            if self._negotiated_algo and self._negotiated_algo != "none":
                algo_display = self._algo_display_name(self._negotiated_algo)
                text += ' / ' + algo_display
            text += '</span>'
        else:
            text = '<span style="color:red;">●</span> ' + self.i18n.tr("dashboard.status_disconnected")

        # 末段：隧道失效（保活探测连续失败）——只有 Cloudflare 模式才有保活，
        # 局域网模式不受 Ready 影响
        if self._mode == TunnelMode.CLOUDFLARE and not self._tunnel_reachable:
            text += ' <span style="color:#c62828;">| ' + self.i18n.tr("dashboard.tunnel_lost") + '</span>'
        self.status_label.setText(text)
        # 波形与接收徽标随连接状态联动：断开即停动画，避免后台空转
        if hasattr(self, "_wave"):
            self._wave.set_active(connected)
            self._wave_status.setText(self.i18n.tr(
                "dashboard.wave_live" if connected else "dashboard.wave_idle"))
            self._wave_status.setProperty("live", "true" if connected else "false")
            self._wave_status.style().unpolish(self._wave_status)
            self._wave_status.style().polish(self._wave_status)

    def set_tunnel_reachability(self, reachable: bool) -> None:
        """隧道公网入口可达性翻转（TunnelKeepalive 探测结果）。

        用状态栏而不是托盘通知——理由是**可见性**，不是「打扰」：失效只会在用户
        不在电脑前时发生，因为人正常使用时保活流量是持续不断的，隧道闲不到被回收
        的条件。而托盘通知只停留几秒，用户不在场等于必然错过，回来时屏幕上没有
        任何痕迹，根本不知道要重扫码。状态栏是常驻的，用户回来一眼就能看到
        「隧道失效，请重启服务」——这才是闭环成立的前提。
        顺带的好处是不抢焦点（60s 一轮，偶发抖动走通知会变成打扰）。
        （发布版没有任何日志出口，这里是唯一可视的落点。）
        """
        if reachable == self._tunnel_reachable:
            return
        self._tunnel_reachable = reachable
        # 复用现有入口重绘整行，避免与加密状态段各自拼一半
        self.update_connection_status(self.connected)

    def _reset_tunnel_reachability(self) -> None:
        """清掉失效标记并重绘状态栏（新域名到手 / 切换模式 / 重启服务时）。

        必须显式重绘：状态栏是自拼的字符串，只改内部状态不会自动刷新，用户会
        继续看到已经过期的「隧道失效」提示——刚做完重启就还挂着这句话最误导人。
        """
        if self._tunnel_reachable:
            return
        self._tunnel_reachable = True
        self.update_connection_status(self.connected)

    def show_hide_on_tray_message(self):
        if getattr(self, '_already_show_hide_on_tray_message', False):
            return
        self._already_show_hide_on_tray_message = True
        self.tray.show_message(
            self.i18n.tr("tray.minimized_title"),
            self.i18n.tr("tray.minimized_message"),
            timeout=3000
        )

    def _quit_app(self):
        """从菜单点击退出，直接终止程序"""
        self._is_force_quitting = True
        QApplication.quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_history_layout") and self._history_rows:
            QTimer.singleShot(0, self._fit_history_rows)
        if hasattr(self, "_toast") and self._toast.isVisible():
            self._position_toast()

    def hideEvent(self, event):
        """主界面收进托盘时，把帮助窗口一起收起来。

        帮助窗口现在是独立顶层窗口（没有父窗口，见 HelpWindow 的说明），Qt 不会再
        帮我们「父隐子隐」。不管的话，主界面缩到托盘了，说明书还孤零零留在桌面上，
        用户既没有入口把它关掉，也不知道程序到底退没退。
        """
        super().hideEvent(event)
        help_window = getattr(self, "_help_window", None)
        if help_window is not None and help_window.isVisible():
            help_window.hide()

    def closeEvent(self, event):
        """重写关闭事件：根据配置决定行为。若为菜单强制退出则直接放行。"""
        # 如果是菜单触发的强制退出，直接放行，不做任何花活
        if getattr(self, '_is_force_quitting', False):
            event.accept()
            return

        close_action = self.sm.get("close_action", None)

        if close_action == "quit":
            event.accept()
            return
        event.ignore()
        if close_action == "tray":
            self.hide()
            self.show_hide_on_tray_message()
        else:
            self._show_close_choice_dialog()

    def _show_close_choice_dialog(self):
        """显示关闭行为选择对话框（首次使用）"""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.i18n.tr("close_choice.title"))
        dialog.setModal(True)
        dialog.setMinimumWidth(350)

        layout = QVBoxLayout(dialog)

        label = QLabel(self.i18n.tr("close_choice.prompt"))
        label.setWordWrap(True)
        layout.addWidget(label)

        quit_radio = QRadioButton(self.i18n.tr("close_choice.quit"))
        tray_radio = QRadioButton(self.i18n.tr("close_choice.tray"))
        quit_radio.setChecked(True)
        layout.addWidget(quit_radio)
        layout.addWidget(tray_radio)

        remember_check = QCheckBox(self.i18n.tr("close_choice.remember"))
        layout.addWidget(remember_check)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() == QDialog.Accepted:
            action = "quit" if quit_radio.isChecked() else "tray"
            if remember_check.isChecked():
                self.sm.set("close_action", action)
            if action == "quit":
                QApplication.quit()
            else:
                self.hide()
                self.show_hide_on_tray_message()
