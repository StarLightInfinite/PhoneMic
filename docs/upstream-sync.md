# 上游同步策略

本项目（StarLightInfinite/PhoneMic）fork 自上游 [franj/PhoneMic](https://github.com/franj/PhoneMic)。本文说明如何跟踪上游更新，既不丢掉上游的功能改进，也不破坏本仓库自己的改动（README 治理、敏感信息清理、CI 等）。

## 远程布局

| 远程名 | 地址 | 用途 |
| --- | --- | --- |
| `origin` | `https://github.com/StarLightInfinite/PhoneMic.git` | 本仓库（发布目标），日常 push 到这里 |
| `upstream` | `https://github.com/franj/PhoneMic.git` | 上游原作者仓库，只 fetch、不 push |

查看配置：`git remote -v`。若 upstream 丢失，用 `git remote add upstream https://github.com/franj/PhoneMic.git` 恢复。

## 同步频率

- **被动**：不设自动合并。每次准备发版前，手动 fetch 一次上游，评估是否有值得合入的改动。
- **触发**：上游有新功能/commit、或本仓库准备打新 tag 时，执行一次「同步检查」（见下）。

## 同步检查流程

```bash
# 1. 拉取上游最新引用（不改动工作区）
git fetch upstream

# 2. 看双方分叉情况：格式为「上游独有  本地独有」
git rev-list --left-right --count upstream/main...main

# 3. 看上游新增了什么
git log main..upstream/main --oneline --stat

# 4. 预检合并是否冲突（只输出，不落盘）
git merge-tree $(git merge-base main upstream/main) main upstream/main \
  | grep -E '^(changed in both|\+<<<<<<<|\+=======|\+>>>>>>>)'
```

## 合并决策

- 上游改动**与本仓库改动不冲突** → 直接 `git merge upstream/main`，跑全量测试 `uv run pytest` 后 push。
- **有冲突** → 通常是双方都改了 `mobile.html`、`locales/*.json`、`settings_manager.py`、`api.py` 这类高频文件。逐文件人工解决，优先保留本仓库的敏感信息治理成果（见下），再跑全量测试。
- 上游某个改动**本仓库不想要** → 不合整条分支，用 `git cherry-pick <commit>` 只挑需要的提交。

## 合并时必须保留的本仓库改动

这些是本仓库相对上游的核心治理成果，**任何同步都不能覆盖**：

- `.gitignore` 对 `/prototype/`、`/shots/`、`*.bak*`、崩溃报告的排除规则，以及 `!/.github/` 例外。
- `README.md` 中指向本仓库（StarLightInfinite）的链接、上游致谢、敏感信息约定。
- `.github/workflows/tests.yml`（CI 配置）。
- 提交身份使用匿名邮箱 `19605592+StarLightInfinite@users.noreply.github.com`，不要带回个人邮箱。
- 不得引入真实内网 IP、临时隧道域名、二维码访问链接等现场数据。

## 当前状态快照（2026-11-16）

- 双方已分叉：`upstream/main` 独有 2 个提交，`main` 独有 2 个提交（本仓库的 README 治理与文档清理）。
- 上游独有提交：
  - `b58bb3a` 手机端新增设置页，输入框下可配快捷按钮行（涉及 mobile.html、settings_manager、api、locales、wire-protocol 等）。
  - `a3f1cd3` 闪电按钮切换写回落盘，键盘/摇杆面板改为清空。
- 预检结果：合并存在冲突（双方都改了高频文件）。是否合入、何时合入由仓库维护者决定，未合入前本仓库保持独立演进。

> 规则：同步是「有选择地吸收上游」，不是「无条件跟随上游」。本仓库的开源发布定位与敏感信息治理优先级高于与上游保持一致。
