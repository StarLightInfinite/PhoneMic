# 双向 config 独立移植预案

> 状态：**方案就绪，未执行**（不改源码、不打 tag、不推送）
> 编写：2026-11-16
> 来源：上游 `franj/PhoneMic` 提交 `b58bb3a` 的「双向 config」部分
> 前置决策：老大已定 tag/Release 自建独立体系；本预案排在**首次独立版本发布之后**

## 1. 目标与范围

把上游的「手机端改配置 → PC 落盘 → 换机/清缓存后首帧带回」这套双向 config 机制摘出来，移植到本仓库。**只跟这一块，不做整包合并。**

### 跟什么 / 不跟什么

| 上游改动 | 是否跟进 | 理由 |
|---|---|---|
| 双向 config 协议层（上行帧 + 白名单 + 规范化 + 恰好回一帧） | ✅ 跟 | 唯一真正的能力增量，本仓库确实没有 |
| `settings_dialog` 订阅变更防覆盖 | ✅ 跟 | 双向 config 的必要配套，不跟会引入"手机改完被 PC 对话框覆盖"的新 bug |
| ⚡ 发送模式落盘修复（`a3f1cd3`） | ❌ 不跟 | 本仓库早已换成"自动/手动"分段控件，localStorage 落盘 + PC 下发都已实现，方案更完整 |
| 手机端设置页 UI（`SettingsPanel`，约 2000 行） | ❌ 不跟 | 是上行帧的"编辑入口"之一，但本仓库可用已有分段控件替代，不必引入整套 UI |
| 可配快捷按钮行 + 3 个新配置键 | ❌ 不跟 | 锦上添花、默认关闭；与本仓库现有界面有差异，看产品口味另排期 |
| 四语 locales 的设置页/快捷按钮文案 | ❌ 不跟 | 只跟 config 用不到这些文案 |
| `wire-protocol.md` 全文重写（1383 行） | ⚠️ 只补 §7 | 上游是整篇格式化重写，照搬会与本地文档大面积冲突；只手工补 config 上行章节 |

### 基线事实（2026-11-16）

- 当前分支 `main`，HEAD = `bb330c9`，本地领先 `origin/main` 1 个 commit（尚未 push）。
- 与上游共同祖先 `6d0469d`；双方各领先 2 commit，已分叉。
- 待移植文件在双方都被改过 → **无法 cherry-pick**，只能手工摘取（见 §5）。

## 2. 分支与版本策略

1. 先把当前 `main` 的工作区改动（CI workflow、upstream-sync 文档、feature-plans 状态表）提交，得到干净的 `main`。
2. 从干净的 `main` 切独立分支：`git checkout -b feat/uplink-config`。
3. 所有移植只在 `feat/uplink-config` 上进行；`main` 保持不动。
4. 本改动作为**独立版本线的一个功能提交**，版本号由老大在首次独立 release 时统一拍板（建议按 feature 递增，不用上游编号）。
5. 全量测试通过后再考虑合并；合并是**另一个对外动作**，另行确认。

## 3. 分文件改动清单

### 3.1 `phonemic/utils/settings_manager.py` — 加白名单与规范化

在模块级（`class SettingsManager` 之前）加入三个常量/函数，从上游照搬但**裁剪白名单**：

```python
MOBILE_MAX_RECORDS_MIN = 5
MOBILE_MAX_RECORDS_MAX = 50
MOBILE_UPLINK_KEYS = ("mobile_max_records", "mobile_send_mode")  # 只留这两个
```

- `normalize_uplink_config(key, value)`：照搬，但删掉 `mobile_show_clear_btn` / `mobile_show_quick_bar` / `mobile_quick_actions` 三个分支（那三个键本仓库不引入）。
- `normalize_quick_actions()`：**整个不要**（属于快捷按钮行）。
- `__init__` 的默认配置：**不要**加上游那 3 个新键。
- `load()` 里的校验：保留 `mobile_max_records` 夹到 5–50 的逻辑（这是通用健壮性）；删掉针对 3 个新键的校验。

> 关键：`settings.json` 是唯一真相源，校验必须落在这里——脏值一旦落盘，之后每次握手都会下发。

### 3.2 `phonemic/server/api.py` — 协议层核心

这是移植的主体，改动相对独立，按上游 `b58bb3a` 的 diff 手工套到本仓库对应位置：

1. **import 区**：加 `from PySide6.QtCore import Qt`；把 `from ... import SettingsManager` 改成同时导入 `MOBILE_UPLINK_KEYS, normalize_uplink_config`。
2. **`ConnectionManager.__init__`（本地 78–102 行）**：把 `self.max_records` / `self.send_mode` 两个字段 + 两个 `connect_changed` 订阅，换成：
   - `self.mobile_config = {k: self.sm.get(k) for k in MOBILE_UPLINK_KEYS}`（同一字典既用于首帧下发，也缓存热推后的值）
   - 订阅 `self.sm.setting_changed.connect(self._on_setting_changed, Qt.ConnectionType.DirectConnection)`
   - ❗ **必须显式 `DirectConnection`**（见 §4.1）
   - 删掉 `_on_max_records_changed` / `_on_send_mode_changed`，换成 `_on_setting_changed(key, new_value)`：只处理在 `mobile_config` 里的键，更新缓存并 `push_config(key, new_value)`。
3. **`connect()` 首帧 config（本地 141–144 行）**：从手写两个字段改成 `frame = {"type": "config"}; frame.update(self.mobile_config); frame["max_frame_size"] = WS_MAX_FRAME_SIZE`。
4. **新增 `_apply_uplink_config(websocket, msg)`**：照搬上游实现（在 `_handle_auth` 之后），完整保留"恰好回一帧"的两分支逻辑（见 §4.2）。
5. **`_handle_client_message`（本地 925 行起）**：在消息类型分派里加 `elif msg_type == "config": await _apply_uplink_config(websocket, inner)`。
6. `push_config()`（本地 700 行）本身不用改。

### 3.3 `phonemic/gui/settings_dialog.py` — 配套防覆盖

照搬上游的 `_sync_mobile_widgets(key, value)`：订阅 `setting_changed`，当 `mobile_max_records` / `mobile_send_mode` 被手机改掉时，同步本对话框里的控件值。

> 不跟这个的后果：PC 设置对话框开着 → 手机改了发送方式 → 回到对话框点"确定" → `_save_settings` 把对话框里的旧值整体写回，把手机刚改的覆盖掉。这是双向 config 引入后必然出现的竞态，属必跟配套。

`QLabel` 的 import 和 `mobile_phone_hint` 提示文案属于设置页改造，**不要**（本仓库对话框这两个控件本来就在，无需加提示）。

### 3.4 `phonemic/resources/mobile.html` — 手机端上行入口（关键决策点）

上游的上行帧发送函数 `sendConfig()`（`{type:'config', key, value}`）是 `SettingsPanel` 类的一部分，与设置页深度耦合；且上游架构是"手机端**不**本地持久化，PC `settings.json` 唯一真相源"，与本仓库现有的 localStorage 落盘**相反**。这里有两个方案，**推荐方案 A**：

**方案 A（推荐）：复用已有分段控件，localStorage 降级为离线缓存**

- 在现有分段控件点击处理（本地 2633–2640 行）里，把 `setAutoMode(..., true)` 改为：发一帧 `{type:'config', key:'mobile_send_mode', value: auto ? 'auto' : 'manual'}`，同时保留 localStorage 写入作为断网时的离线缓存。
- 首帧 config 下发（本地 5330–5333 行）保持从 PC 读 `mobile_send_mode` 并 `setAutoMode()`，使 PC 成为重连后的真相源 → 实现"换机/清缓存后设置带回"。
- 改动量：约 3–5 行 JS，不引入设置页 UI，不动 2000 行大改。
- 效果：核心价值闭环（手机改模式会写回 PC，换机不丢），代价最小。

**方案 B（保守）：只做协议层，手机端暂不发上行帧**

- 后端具备双向能力，但手机上没有触发入口 → 用户暂时感知不到变化，只为后续设置页铺路。
- 改动量最小，但"换机不丢配置"的目标当下无法验证。

> 两个方案都不引入快捷按钮行和设置页。方案 A 是本次"跟进双向 config"能兑现价值的最小路径。

### 3.5 `phonemic/resources/locales/*.json` — 基本不动

双向 config 本身不产生面向用户的新文案。`settings_dialog` 的 hint 文案若按 §3.3 跳过，则四语文件都不用改。

### 3.6 `docs/wire-protocol.md` — 只补 §7

在 config 帧章节手工补一小节"config 上行"：

- 帧形状：手机 → PC 发 `{"type":"config","key":<白名单键>,"value":<值>}`。
- 契约：无论落盘成功或被拒，PC **恰好回一帧**该键的真实值（扁平单键形状）；白名单外的键只记日志、不回帧。
- 不要照搬上游 1383 行的整篇重写。

### 3.7 `tests/` — 摘取核心用例

从上游 `tests/test_backend.py` 摘 `TestUplinkConfig` 的核心断言，按本仓库白名单裁剪：

| 用例 | 摘取 | 裁剪点 |
|---|---|---|
| 首帧 config 携带全部 `MOBILE_UPLINK_KEYS` | ✅ | 断言只覆盖两个键 |
| 合法值变化 → 恰好一帧回声 | ✅ | 用 `mobile_send_mode` / `mobile_max_records` |
| 非法值（类型错、超范围）→ 回真实值 | ✅ | 去掉快捷按钮相关的非法值用例 |
| 相同值 → 仍回一帧 | ✅ | 不变 |
| 白名单外键 → 不落盘、不回帧 | ✅ | 用例里的 `auth_method` 可保留 |
| 快捷按钮列表规范化（`normalize_quick_actions`） | ❌ | 属于不跟的部分 |

上游 `tests/test_mobile.py`、`test_settings_dialog.py`、`test_settings_manager.py` 里的设置页/快捷按钮用例一律不摘。

## 4. 三个必须踩准的技术点

### 4.1 `DirectConnection` 防死锁

`setting_changed` 默认是 `AutoConnection`，会把回调排队到 GUI 线程。手机上行 config 由服务端 asyncio 线程写盘，若 GUI 线程忙（或测试环境根本没有 Qt 事件循环），回声帧永远发不出去，手机端就卡在乐观更新的错误状态。**必须**对本模块的订阅显式指定 `Qt.ConnectionType.DirectConnection`。回调只做"缓存 + 推帧"，不碰 Qt 控件，跨线程安全。

### 4.2 "恰好回一帧"契约

手机端依赖它做"以 PC 为准"的自愈：

- 值合法且与磁盘不同 → 交给 `sm.set()`，由 `setting_changed` 热推回调发唯一一帧回声，函数直接返回。
- 值非法或与磁盘相同 → `set()` 不发信号，主动补一帧当前真实值。
- 白名单外键 → 只记日志、不回帧（旧版手机页面不该被惩罚）。

回帧形状必须是扁平单键 `{"type":"config", <key>: <value>}`，与首帧、`push_config` 一致；**不能**写成 `{key, value}` 两字段。

### 4.3 白名单要在服务端兜底

手机端不校验也能走到服务端，脏数据绝不能穿过规范化层进入 `settings.json`。校验放 `settings_manager.normalize_uplink_config`，不在手机端 JS 里做（手机端可被绕过）。

## 5. 冲突解法

本仓库与上游**双方都改过**这些文件，无法 `git cherry-pick b58bb3a`（会大面积冲突）。做法：

1. 在 `feat/uplink-config` 分支上，用 `git show b58bb3a -- <file>` 看上游 diff，按 §3 清单**手工编辑**本仓库文件。
2. 优先改"改动独立、冲突小"的文件：`settings_manager.py` → `api.py` → `settings_dialog.py` → `mobile.html` → `wire-protocol.md` → 测试。
3. `mobile.html` 是本仓库与上游都大改的文件，**只按方案 A 加 3–5 行**，绝不整段套用上游版本。
4. 每改完一个文件就跑一次针对性测试，不要攒到最后。

## 6. 验证清单

- [ ] `uv run pytest` 全量通过（基线 1228 条 + 新增用例）。
- [ ] 首帧 config 携带 `MOBILE_UPLINK_KEYS` 全部键。
- [ ] 手机改发送方式 → PC `settings.json` 的 `mobile_send_mode` 随之改变。
- [ ] 非法值被拒且只回一帧真实值；白名单外键不回帧。
- [ ] 换设备/清缓存后重连，手机端设置由首帧带回（方案 A 才能验证这一条）。
- [ ] PC 设置对话框开着时手机改配置，对话框控件同步、点确定不覆盖。
- [ ] 敏感扫描：新增代码无密钥/真实 IP/本机路径；测试里的示例 IP 用文档占位值。

## 7. 回滚方案

所有改动都在 `feat/uplink-config` 分支，`main` 不动：

- 验证不通过 → 直接删除分支：`git branch -D feat/uplink-config`。
- 已合并后才发现问题 → `git revert <merge-or-commit>`，不用 reset 改写已推送历史。
- 因 `settings.json` 结构未变（不引入新键），回滚不涉及用户配置迁移。

## 8. 待老大拍板的点

1. **手机端方案**：推荐方案 A（复用分段控件 + 上行写回，localStorage 降级为离线缓存）；保守方案 B 只铺路。
2. **执行时机**：本预案排在首次独立版本发布之后；是否提前，由老大定。
3. **合并与推送**：本预案只写方案；真正动手、合并、打 tag、推送都是后续独立动作，另行确认。
