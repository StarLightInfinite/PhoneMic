"""局域网入口发现服务：让安卓壳 APP 免扫码拿到当前连接地址。

背景：入口 URL 是动态的——LAN 模式可能是 IP/端口变化，URL-fragment 模式
带随机 secret 路径，Cloudflare 模式每次启动换域名，点「刷新」还会换密钥对。
H5 用户靠重新扫码解决，APP 则用本服务在 WLAN 内自动找到 PC。

协议（UDP，默认端口 12001）：
    请求: ASCII ``PHONEMIC-DISCOVER-1\\n``（18 字节，超过 1 KiB 的包直接丢弃）
    响应: UTF-8 JSON ``{"v": 1, "url": "<完整入口 URL>", "ts": <unix 秒>}``

安全边界：
    - 只回「当前入口 URL」这一项公开信息，不回私钥、种子或任何密钥材料。
      E2EE 认证（扫码 fragment / TOFU 识别码审批）完全走既有流程，本服务
      不改变信任模型。
    - URL 由外部 provider 现算，来源与二维码一致（都经
      ``SecureChannel.append_to_url``）。刷新/换端口/切模式后无需重启本服务，
      下一条探测自动拿到新值。
    - 只应答 magic 精确匹配的探测；UDP 无连接，靠 magic 挡住端口扫描器。
    - 绑定失败（端口占用 / 防火墙）只记日志，绝不影响主程序启动。
"""

import ipaddress
import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 12001
DISCOVERY_MAGIC = b"PHONEMIC-DISCOVER-1\n"
_MAX_PACKET = 1024
_PROTOCOL_VERSION = 1


class DiscoveryService:
    """UDP 发现服务：收到合法探测就回当前入口 URL。

    Args:
        url_provider: 无参回调，返回当前完整入口 URL（含 secret 路径与
            fragment）。返回空串表示服务尚未就绪，此时不应答。
        port: 监听端口；0 表示由系统分配（测试用），实际端口见 :attr:`port`。
    """

    def __init__(self, url_provider: Callable[[], str], port: int = DISCOVERY_PORT):
        self._url_provider = url_provider
        self._bind_port = int(port)
        self.port = int(port)
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stopped = True
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return not self._stopped and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """启动发现服务。返回是否成功绑定并开始监听。"""
        with self._lock:
            if not self._stopped and self._thread is not None:
                return True
            self._stopped = False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.5)
            sock.bind(("0.0.0.0", self._bind_port))
        except OSError as exc:
            with self._lock:
                self._stopped = True
            logger.warning("局域网发现服务启动失败（端口 %s）: %s", self._bind_port, exc)
            return False

        self._socket = sock
        self.port = sock.getsockname()[1]
        self._thread = threading.Thread(
            target=self._serve, name="phonemic-discovery", daemon=True
        )
        self._thread.start()
        logger.info("局域网发现服务已启动，UDP 端口 %s", self.port)
        return True

    def _serve(self) -> None:
        assert self._socket is not None
        sock = self._socket
        while not self._stopped:
            try:
                data, addr = sock.recvfrom(_MAX_PACKET)
            except socket.timeout:
                continue
            except OSError:
                if self._stopped:
                    break
                logger.exception("局域网发现服务接收失败")
                break

            if data != DISCOVERY_MAGIC:
                continue

            # 只应答RFC1918 私网来源，挡住来自公网的探测（服务本身仍监听
            # 0.0.0.0 以接收 WLAN 广播）。解析失败按私网处理，避免误伤
            # Windows 上偶发的 0.0.0.0 / 链路本地地址。
            try:
                if not ipaddress.ip_address(addr[0]).is_private:
                    continue
            except ValueError:
                pass

            try:
                url = self._url_provider()
            except Exception as exc:  # provider 故障不该拖死发现线程
                logger.warning("入口 URL 提供器异常: %s", exc)
                continue
            if not url:
                continue

            payload = json.dumps(
                {"v": _PROTOCOL_VERSION, "url": url, "ts": int(time.time())},
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                sock.sendto(payload, addr)
            except OSError as exc:
                logger.debug("发现响应发送失败（%s）: %s", addr, exc)

    def stop(self) -> None:
        """停止服务并释放端口（幂等）。"""
        with self._lock:
            self._stopped = True
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._socket = None
        self._thread = None
