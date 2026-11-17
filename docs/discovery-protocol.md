# 局域网入口发现协议

给安卓壳 APP 用；H5 不需要，也不受影响。

## 传输

UDP，固定端口 **12001**。PC 端 `phonemic/server/discovery.py` 监听 `0.0.0.0:12001`，APP 向 `255.255.255.255:12001` 广播探测。

## 报文

**请求**（ASCII，18 字节）：

```
PHONEMIC-DISCOVER-1\n
```

**响应**（UTF-8 JSON）：

```json
{"v": 1, "url": "http://192.168.1.10:12000/", "ts": 1760000000}
```

| 字段 | 含义 |
|---|---|
| `v` | 协议版本，当前 1 |
| `url` | 当前完整入口 URL，可直接 WebView 加载 |
| `ts` | PC 当前 Unix 秒，仅用于诊断 |

## URL 的来源

`url` 由 `Dashboard.get_entry_url()` 现算，与 PC 主界面二维码**同源**：

- LAN 模式 → `http://{lan_ip}:{lan_port}`
- Cloudflare 模式 → 当前 `https://*.trycloudflare.com`
- 再经 `SecureChannel.append_to_url()`：TOFU 模式原样返回；URL-fragment 模式追加 `/{secret_path}/` 和 `#k=<PC公钥>&a=<算法列表>`。

因为每次应答都现算，点「刷新」、换端口、切模式后**无需重启发现服务**，下一条探测自动拿到新地址。

## 行为约束

- 只应答 magic 精确匹配、长度 ≤ 1 KiB 的包；只回私网来源。
- provider 未就绪（返回空串）或抛异常时不应答，且不结束服务线程。
- 端口占用 / 绑定失败只写日志，不影响 PhoneMic 主程序启动。
- 只回 URL，不回任何密钥材料；E2EE 握手完全由 `mobile.html` 与服务端按既有流程完成。
- 服务随主程序退出而停止（`on_quit` 调 `DiscoveryService.stop()`）。
