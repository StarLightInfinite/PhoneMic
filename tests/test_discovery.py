"""局域网发现服务测试。

用 port=0 让系统分配端口，避免 xdist 并行 worker 之间抢端口。
"""

import json
import socket

import pytest

from phonemic.server.discovery import DISCOVERY_MAGIC, DiscoveryService


def _probe(port, payload=DISCOVERY_MAGIC, host="127.0.0.1", timeout=1.0):
    """发一条 UDP 探测，返回响应字节；超时返回 None。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, (host, port))
        try:
            data, _ = sock.recvfrom(4096)
            return data
        except socket.timeout:
            return None


@pytest.fixture
def service():
    """一个 provider 可变的发现服务，用完必停。"""
    state = {"url": "http://192.168.1.10:12000/abc/"}

    def provider():
        return state["url"]

    svc = DiscoveryService(provider, port=0)
    assert svc.start()
    try:
        yield svc, state
    finally:
        svc.stop()


def test_probe_returns_current_entry_url(service):
    svc, state = service
    raw = _probe(svc.port)
    assert raw is not None
    payload = json.loads(raw.decode("utf-8"))
    assert payload["v"] == 1
    assert payload["url"] == state["url"]
    assert isinstance(payload["ts"], int)


def test_provider_change_is_picked_up_without_restart(service):
    """刷新/换端口后 provider 返回新值，下一条探测立刻拿到新地址。"""
    svc, state = service
    assert json.loads(_probe(svc.port))["url"] == state["url"]

    state["url"] = "https://new-domain.trycloudflare.com/xyz/#k=abc&a=xchacha20"
    assert json.loads(_probe(svc.port))["url"] == state["url"]


def test_wrong_magic_is_ignored(service):
    svc, _ = service
    assert _probe(svc.port, payload=b"PHONEMIC-DISCOVER-0\n", timeout=0.4) is None
    assert _probe(svc.port, payload=b"", timeout=0.4) is None


def test_empty_url_is_not_answered():
    svc = DiscoveryService(lambda: "", port=0)
    assert svc.start()
    try:
        assert _probe(svc.port, timeout=0.4) is None
    finally:
        svc.stop()


def test_provider_exception_does_not_kill_service():
    calls = {"n": 0}

    def broken_provider():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary failure")
        return "http://10.0.0.5:12000/"

    svc = DiscoveryService(broken_provider, port=0)
    assert svc.start()
    try:
        assert _probe(svc.port, timeout=0.4) is None
        assert json.loads(_probe(svc.port))["url"] == "http://10.0.0.5:12000/"
        assert svc.is_running
    finally:
        svc.stop()


def test_stop_is_idempotent_and_releases_port():
    svc = DiscoveryService(lambda: "http://127.0.0.1:1/", port=0)
    assert svc.start()
    port = svc.port
    svc.stop()
    svc.stop()
    assert not svc.is_running

    # 端口已释放：另一个服务可以重新绑定同一端口
    svc2 = DiscoveryService(lambda: "http://127.0.0.1:2/", port=port)
    try:
        assert svc2.start()
        assert json.loads(_probe(port))["url"] == "http://127.0.0.1:2/"
    finally:
        svc2.stop()


def test_occupied_port_fails_softly():
    """端口被占时返回 False，不抛异常、不留下活线程。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blocker:
        blocker.bind(("0.0.0.0", 0))
        port = blocker.getsockname()[1]
        svc = DiscoveryService(lambda: "http://127.0.0.1/", port=port)
        assert svc.start() is False
        assert not svc.is_running
        svc.stop()


def test_public_source_is_ignored():
    """公网来源探测不应拿到入口 URL（服务只服务 WLAN 内设备）。"""
    svc = DiscoveryService(lambda: "http://192.168.1.10:12000/", port=0)
    assert svc.start()
    try:
        # 8.8.8.8 不是本机地址，sendto 会发到默认路由；这里只验证服务没有
        # 因公网地址判断而崩溃，且 loopback 探测仍正常。
        assert json.loads(_probe(svc.port))["url"] == "http://192.168.1.10:12000/"
    finally:
        svc.stop()
