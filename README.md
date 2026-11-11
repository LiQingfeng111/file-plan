# File Plan · 文件操作计划书

> **你批准的计划，和 AI 实际执行的计划，怎么证明是同一个？**
>
> 把「执行前预览」变成一份可归档、可复核、哈希绑定的计划书。零依赖、纯本地、平台无关。

[English summary](#english-summary) · [快速开始](#快速开始) · [安全模型](#安全模型)

## 为什么做这个

让 AI 整理文件已经很常见了：归档下载文件夹、批量重命名照片、清理重复文件。问题不在它会不会做错，而在**留不下证据**：

- 预览列表在聊天窗口里滚过去了，执行时多动了两个文件，事后无从对证；
- "我确认"之后，助手临场调整了范围（"顺手把空目录也清了"），你并不知道；
- 删除是不可逆的，而多数工具只给一个「确定」按钮；
- 换会话之后，谁也说不清当时到底改了什么、备份在哪。

现有做法分两类，都不够：

| 已有方案 | 局限 |
| --- | --- |
| 商业 AI 文件管理软件（FileNeatAI、Riffo、AiPy 等）的「执行前预览」 | App 内一次性 UI 状态，不能归档、不能复核、无法绑定批准版本 |
| `rsync --dry-run`、Terraform plan 等运维工具 | 面向专业运维，不产出人可审阅的中文影响清单，也不做备份/隔离区/执行复核 |
| 通用脚本（自己写 `shutil`） | 没有审批留痕，AI 说改就改 |

GitHub 上按 `file operations dry run plan json agent`、`batch rename preview confirm cli`、`safe file operations plan hash approval` 检索，均无对应实现。`file-plan` 填的是这个空：**计划哈希绑定 + 人可读影响清单 + 备份与隔离区 + 执行后复核**。

## 核心设计

1. **计划书优先**：`PLAN.md` 逐项列出操作、源/目标、大小、危险等级、冲突与理由，交人审阅。
2. **哈希绑定**：`plan.json` 的操作列表算出 `token`，写进计划书；`apply` 必须带上它，执行前重算 —— 计划被动过就拒绝执行。
3. **确认留痕**：`--confirm` 记录用户原话，写进 append-only 事件日志；AI 不得代填。
4. **永不真删**：`delete` 把文件移到 `.fileplan/trash/<token>/`，保留原相对路径；技能不提供清空隔离区的功能。
5. **先备份再改动**：`move` / `rename` / 覆盖前把源文件复制到 `.fileplan/backup/<token>/`。
6. **可复核**：`apply.json` 记录每项结果，`verify` 逐项检查实际状态。

## 快速开始

### 安装

**WorkBuddy 技能**：

```bash
git clone https://github.com/LiQingfeng111/file-plan.git \
  "$HOME/.workbuddy-ai/skills/file-plan"
```

重启会话后，说「帮我把下载文件夹整理一下，先给我看看会动哪些文件」即可触发。

**独立 CLI**：

```bash
python -X utf8 scripts/plan.py init --root ./my-files --name "整理下载"
```

### 完整流程

```bash
R=./my-files
P=scripts/plan.py

# 1. 建计划（此时不动任何文件）
python -X utf8 $P init --root $R --name "归档 2026 年发票"
python -X utf8 $P add --root $R --op move --src "发票_01.pdf" --dst "归档/2026/发票_01.pdf" --note "按年份归档"
python -X utf8 $P add --root $R --op move --src "发票_02.pdf" --dst "归档/2026/发票_02.pdf" --note "按年份归档"
python -X utf8 $P add --root $R --op delete --src "临时缓存.tmp" --note "确认无用"

# 2. 生成人可读影响清单，把 PLAN.md 交给用户审阅
python -X utf8 $P build --root $R

# 3. 用户确认后，用 PLAN.md 里的 token 执行
python -X utf8 $P apply --root $R --token 3f9a... --confirm "我看过清单，同意执行"

# 4. 复核
python -X utf8 $P verify --root $R
```

`PLAN.md` 大致长这样：

| # | 操作 | 源 | 目标 | 大小 | 危险 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | move | `发票_01.pdf` | `归档/2026/发票_01.pdf` | 82 KB | 中 | OK |
| 2 | move | `发票_02.pdf` | `归档/2026/发票_02.pdf` | 77 KB | 中 | OK |
| 3 | delete | `临时缓存.tmp` | → 隔离区 | 4 KB | **高** | OK |

底部给出计划哈希、统计、冲突清单与确认命令模板。

## 安全模型

| 风险 | 防护 |
| --- | --- |
| 计划被临场改动 | `token = SHA-256(规范化操作列表)`，apply 前重算比对 |
| 越界操作 | 只接受工作区内相对路径；拒绝绝对路径、盘符、`..`、符号链接越界、指向 `.fileplan/` 自身 |
| 误删 | `delete` 只接受文件（不接受目录），移动到隔离区，永不真删 |
| 覆盖已有文件 | 默认判冲突并拒绝执行，需 `add --overwrite` 显式允许 |
| 大规模误操作 | 单计划默认上限 50 项，超出需 `--limit` 显式提高 |
| 执行中途失败 | 立即停止，已完成/失败/跳过逐项记录，不重试不跳过 |
| 事后无法回溯 | 备份目录、隔离区路径、确认原话、事件日志全部落盘 |
| 无授权执行 | `apply` 必须同时给 `--token` 与非空 `--confirm` |

**不做的事**：不递归删除目录、不清空隔离区、不执行 shell 命令、不联网、不调用模型、不自动判断哪些文件"该删"（语义由人决定）。

## 目录结构

```
<工作区>/
├── PLAN.md                 # 人可读影响清单
└── .fileplan/
    ├── plan.json           # 操作列表 + token（事实源）
    ├── apply.json          # 执行结果
    ├── events.jsonl        # append-only 事件日志
    ├── backup/<token8>/    # 执行前备份，保持原相对路径
    └── trash/<token8>/     # delete 隔离区，保持原相对路径
```

`backup/` 与 `trash/` 建议加入 Git 忽略；`PLAN.md` 与 `plan.json` 可以提交归档，作为「当时批准了什么」的证据。

## 仓库内容

```
SKILL.md                WorkBuddy 技能入口
README.md               本文件
scripts/plan.py         确定性 CLI（仅标准库）
examples/               演练脚本与示例计划书
tests/test_plan.py      单元测试
```

## 配套项目

- [`agent-receipt`](https://github.com/LiQingfeng111/agent-receipt)：管**任务产物**的可复验验收与跨会话交接。
- `file-plan`：管**文件副作用**的预演、审批与执行。
- [`workbuddy-webnovel-writer`](https://github.com/LiQingfeng111/workbuddy-webnovel-writer)：长篇网文写作工作台。

三者共享「文件即事实源、不信任自述、确定性可复验」的设计哲学，互不依赖。

## FAQ

**Q：和 `rsync --dry-run` 有什么区别？** 后者输出终端文本，面向运维；本工具产出可归档的中文影响清单、绑定批准版本的哈希、自动备份与隔离区、执行后复核，面向「人审批 AI 的动作」。

**Q：能恢复吗？** 能。`apply.json` 记录了备份与隔离区的确切路径，按原相对路径取回即可；工具本身不提供一键回滚，避免自动改动再次失控。

**Q：为什么 delete 不真删？** 因为删除不可逆。隔离区让人有机会反悔，代价只是磁盘占用。

**Q：token 泄露了怎么办？** token 不是凭证，只是计划内容的指纹；没有 `--confirm` 与本地文件访问权，拿到 token 也执行不了。

**Q：能整理桌面 / 下载文件夹吗？** 可以，但先只读盘点、生成计划书、由人确认。模糊指令（"清理一下电脑"）应先问清目录、类型与判定条件。

## English Summary

A local-first, zero-dependency CLI and WorkBuddy skill that turns AI file operations into a reviewable, hash-bound plan. `build` emits a human-readable impact sheet (PLAN.md) plus a token derived from the operation list; `apply` requires both that exact token and the user's confirmation text, re-computes the hash before touching anything, backs up affected files, and moves deletions to a quarantine folder instead of deleting. `verify` re-checks every operation. No shell, no network, no model calls.

## 许可证

[MIT](LICENSE) © 2026 LiQingfeng111
