# Codex 子智能体状态校准器

`reconcile-codex-subagents` 是一个精简、只读的 Agent Skill，用于校准经
脱敏的 Codex 子智能体快照。它把生命周期证据与界面计数进行比较；如果假
运行、陈旧行或 compact / resume 不一致再次出现，就让验收失败。

仓库 slug：`codex-subagent-reconciler`。

这是非官方社区项目，不是 Codex Desktop 创建假子智能体的永久上游修复。
这里的自检/预防是检测复发并让验收失败，并不能阻止 Desktop 创建假运行。

## 安装

使用 `$skill-installer` 从 `OneBigMoon/codex-subagent-reconciler` 安装，路径
为 `skills/reconcile-codex-subagents`。手动安装时，将该目录复制到 Codex
skills 目录。

## 触发示例

当出现 ghost、stale 子智能体、running 计数不一致，或 compact/resume 不一
致时使用。例如：

- “检查这份脱敏快照里的 ghost 子智能体计数。”
- “排查 compact/resume 后仍显示 running 的行。”
- “校准假运行的子智能体计数。”
- “检查 compact / resume 后的子智能体状态。”

不要提供原始日志、数据库、截图、凭据或猜测的 ID。

## 快照格式

输入是显式 JSON，必须包含 `schema: codex-subagent-snapshot/v1`：

```json
{
  "schema": "codex-subagent-snapshot/v1",
  "ui_counts": {"active": 1, "done": 0},
  "agents": [{
    "id": "synthetic-agent-1",
    "name": "synthetic-worker",
    "ui_state": "active",
    "live_state": "running",
    "events": [{"seq": 1, "kind": "task_started", "source": "test"}],
    "exact_target": "synthetic-agent-1"
  }]
}
```

快照中只能放合成或脱敏数据。

## 命令

```sh
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json --json
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json --json --show-identifiers
python3 skills/reconcile-codex-subagents/scripts/postflight.py \
  --before before.json --after after.json --target synthetic-agent-1 --json
```

两个工具都是只读的，不扫描、不轮询、不联网、不写文件。doctor 默认给 ID
和名称使用别名；仅在明确传入 `--show-identifiers` 时显示原值。

退出码：`0` 表示一致/通过，`1` 表示计数或 postflight 失败，`2` 表示输入
无效、不支持、含歧义或结果无法判定。
单一终态信号不足：终态生命周期证据必须由规范化的终态 live_state 或两个
不同事件来源共同证明。

## 生命周期边界

使用官方可调用工具生成脱敏快照，运行 doctor，再运行 postflight。只有用户
明确授权一个精确的、已确认的 ghost，且存在官方生命周期 API 时，才可以通
过该 API 执行一次关闭；绝不编写脚本关闭。最后重新读取界面验证。禁止写
SQLite、修改 `app.asar`、猜测 ID、归档/删除、批量中断、上传原始数据，或
仅凭单一证据宣称终态。

永久修复应在上游：Desktop 需要稳定的生命周期契约，并在 compact/resume 后
重新校准界面状态。本 Skill 只提供确定性的验收检查。
