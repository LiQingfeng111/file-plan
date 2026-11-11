# 示例计划书

`demo-workspace/` 是一份**只做到 build、尚未执行**的示例：三个假文件、三项操作、一份人可读的 `PLAN.md`。刻意不执行，这样仓库里始终保留「待用户确认」的状态，方便直接查看计划书长什么样。

| 文件 | 看什么 |
| --- | --- |
| `PLAN.md` | 影响清单：逐项操作、大小、危险等级、预检结果、token、确认命令模板 |
| `.fileplan/plan.json` | 机器可读操作列表 + token（事实源） |
| `.fileplan/events.jsonl` | init → add ×3 → build 的事件链 |
| `发票_01.pdf` / `发票_02.pdf` / `临时缓存.tmp` | 演练用的占位文件（内容为纯文本） |

示例中的绝对路径已替换为相对路径；实际使用时 `PLAN.md` 会显示你本机的工作区绝对路径。

## 自己跑一遍

在仓库根目录：

```bash
R=./examples/demo-workspace
P=scripts/plan.py

# 查看当前计划
python -X utf8 $P status --root $R

# 审阅 PLAN.md 后，用文件里的 token 执行（请换成你自己的确认原话）
python -X utf8 $P apply --root $R --token fd82509ef0c03a9a --confirm "我看过清单，同意执行"

# 复核
python -X utf8 $P verify --root $R
```

执行后：

- 两个「发票」文件被移动到 `归档/2026/`；
- `临时缓存.tmp` 被移进 `.fileplan/trash/<token8>/`，**没有真正删除**；
- 受影响文件的备份在 `.fileplan/backup/<token8>/`；
- 结果记录在 `.fileplan/apply.json`。

想验证 token 绑定：随便改一条 `plan.json` 里的 `dst`，再运行 `status`，会提示 token 失效；此时 `apply` 会被拒绝，必须重新 `build` 并重新取得确认。

> 提示：`.gitignore` 已排除 `.fileplan/backup/` 与 `.fileplan/trash/`，避免把备份内容提交到仓库。
