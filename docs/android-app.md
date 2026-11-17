# 安卓壳 APP（与 H5 并存）

## 定位

这是**新增**的手机端入口，旧的 H5 方案完全保留。两者连的是同一台 PC、同一套 `mobile.html` 页面、同一条 E2EE 通道，功能没有差别。

| | H5 | 安卓 APP |
|---|---|---|
| 安装 | 无 | 装一次 APK |
| 打开方式 | 找链接 / 翻扫码记录 | 桌面图标点开 |
| 连上电脑 | 扫码或输地址 | 同一 WiFi 自动发现；外网扫一次码 |
| 页面样式更新 | 刷新页面 | **自动生效，不用重装** |

## 为什么是「壳」而不是原生界面

入口地址是动态的：LAN 的 IP / 端口会变，URL-fragment 模式带随机 secret 路径，Cloudflare 模式每次启动换域名，点 PC 主界面的「刷新」还会换密钥对与路径。把界面写死进 APK，等于每次地址变化都要重装。

所以 APK 只做三件事：

1. 桌面常驻图标，点开进全屏 WebView；
2. 启动时在 WLAN 内用 UDP 找到 PC 当前入口 URL（见 [发现协议](discovery-protocol.md)）；
3. 找不到就用上次缓存地址，仍连不上则扫 PC 主界面的二维码。

页面内容 100% 由 PC 端 HTTP 服务实时提供（`cacheMode = LOAD_NO_CACHE`）。**改 CSS、改布局、改交互、改文案，都不会触发 APK 重装**——除非改的是壳本身的发现逻辑、文件选择器或权限适配。

## 使用流程

- **同一 WiFi（日常）**：PC 启动 PhoneMic → 手机点图标 → 自动发现并打开页面。点 PC 上「刷新」或改端口后，再点一次图标即可，不用重新扫码。
- **外网 / Cloudflare 模式**：点图标 → 自动发现失败 → 扫 PC 主界面二维码 → 打开公网地址并缓存。域名变了再扫一次。
- **重新连接**：按手机返回键 → 弹菜单选「重新扫码连接」。
- **首次人工认证（TOFU）**：和 H5 一样，手机显示 4 位识别码，在 PC 上核对并点「允许」。之后重连自动走 TOFU 重连路径。

> 「常驻」指桌面图标常驻、点开即用，不是微信式后台保活。语音输入时仍需打开 APP 页面，这一点与 H5 相同。

## 构建与安装

本机不需要 Android SDK。构建走 GitHub Actions：

1. 在 Actions 页手动运行 `android-build`，或推 `v*` tag 自动触发；
2. 跑完在本次运行的 Artifacts 里下载 `PhoneMic-Android`，解压得到 `app-debug.apk`；
3. 传到手机，允许安装未知来源应用后安装。

APK 用 Gradle 调试密钥签名（公开仓库不适合放正式 keystore），属于自签分发，未来如要上架商店再补签名与上架材料。

想本地构建也可以：`cd android && gradle assembleDebug`（需 JDK 17 + Android SDK 35，并接受 SDK licenses）。

## 权限说明

| 权限 | 用途 |
|---|---|
| INTERNET | 访问 PC 服务 |
| ACCESS_WIFI_STATE / CHANGE_WIFI_MULTICAST_STATE | 取 MulticastLock，可靠收到 UDP 应答 |
| CAMERA | 扫一扫兜底 |

网络配置里全局放行 cleartext，因为 LAN 模式入口是 `http://192.168.x.x:端口`；E2EE 在 HTTP 之上独立加密，链路上跑的是密文。

## 安全边界

- 发现服务只回**当前入口 URL**，不回私钥、种子或任何密钥材料；E2EE 认证仍走既有流程（扫码 fragment / TOFU 识别码审批），信任模型未变。
- 入口 URL 里的 secret 路径等价于一张电子二维码，缓存在应用私有 SharedPreferences 里，未 root 的设备上其他应用读不到。
- 发现服务只应答 magic 精确匹配的 UDP 包，且只回私网来源。同网段设备只要知道协议就能拿到入口 URL——这与把二维码投在屏幕上等价，不是新暴露面；介意的话把 UDP 12001 入站封掉，APP 会自动退到扫一扫。

## 已知限制

- **Windows 防火墙**：首次启动发现服务时，若系统弹出「是否允许 Python 访问网络」，需勾选专用网络并允许；否则 UDP 探测被拦，APP 会退到扫一扫。
- **外网必须扫码**：UDP 发现只在同一个 WLAN 内有效，Cloudflare 的临时域名每次启动都变，外网场景无法自动跟随。
- **后台不保活**：APP 退到后台后页面会暂停，WebSocket 可能断开；回到前台由 `mobile.html` 自身重连逻辑处理。
