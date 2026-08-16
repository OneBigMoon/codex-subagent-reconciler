# Codex Workflow Guardian

`codex-workflow-guardian` 是仅面向 macOS 的 Codex Plugin/Skill 工作流，不是
MCP server：它没有实时远程工具、数据或认证边界。它组合 Guardian 编排、严格只读的
`reconcile-codex-subagents` Reconciler，以及必须显式调用的
`setup-codex-workflow-guardian` Setup Skill。旧仓库 slug 是
`codex-subagent-reconciler`。

这是非官方社区项目，不是 OpenAI 官方产品。

语言：[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) |
[Español](README.es.md)

支持平台：仅 macOS。核心 Plugin 安装只需要支持 Plugin marketplace 的 Codex CLI，本身不需要
Python。运行任何捆绑脚本需要 Python 3.9+；执行 `portable-full --apply` 需要 Python 3.11+。
manifest 没有 OS 安装门，因此其他操作系统可能发现或安装该 Plugin；这不代表受支持。每个捆绑的
可执行 Skill 在非 Darwin 主机上都会在加载 snapshot 或进行受管读写前 fail closed。支持范围仍仅为
macOS。

## 三步心智模型与最短路径

1. 将用户已经安装并信任的普通 `codex` 命令作为 pre-Setup trust boundary。用已审计的不可变
   SHA 注册 GitHub-backed marketplace 并安装 Guardian Plugin。这会提供捆绑的三个 Guardian
   Skill；不会复制凭据、MCP 配置或私有 OMC。

   ```sh
   GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"
   codex plugin marketplace add OneBigMoon/codex-subagent-reconciler \
     --ref "$GUARDIAN_REF" --json
   codex plugin add codex-workflow-guardian@onebigmoon-codex-workflows --json
   ```

2. 开始新的 Codex 任务，显式调用 `$setup-codex-workflow-guardian`。Skill 会先校验其自身捆绑的 exact
   versioned cache 安装路径 `E`（canonical root），要求 clean、不变更且 exact-ref 一致；再从
   `codex plugin list --json` 读取唯一 Guardian selector 行，并将该行的 `source.path`
   作为 `P`，校验 `P` 的 canonical marketplace 来源，并检查 `P` 与 `E` 的 exact-ref
   一致后，从 `E` 读取并校验 Plugin JSON；从固定 macOS 候选发现 Python，必要时静态解析受支持的
   Codex wrapper。
   CLI `0.146.0` 的 `plugin list --json` 记录可能不含 `installedPath`，不得用 glob/scan
   目录、猜测或重跑 `plugin add` 来恢复路径。不要手工解析 JSON 或填写 native/Python 路径。
   先审查 public `--check`
   projection，再在即时授权后执行一次 `--apply`。这次 apply 会把固定版本的 All in Luna
   Plugin、确切 CLI wheel 和 14 个脱敏角色模板自动安装到选定的持久 `CODEX_HOME`。Ponytail、
   Headroom、Node、生命周期 hooks 和 HostAdapter relay 仍是可选、另行授权的增强项。Setup 添加的
   Plugin（包括 All in Luna）保持 `installed-but-unowned`；receipt ownership 仅适用于已证明由本次
   Setup 管理的角色文件和 venv。
3. 之后每个交付只调用 `$codex-workflow-guardian`。All in Luna 能力已验证时复用一个匹配的
   持久运行。只有在尚无 owner 且 fresh canonical zero-match 证明完成后，才允许受约束的原生/手动降级；
   已有 owner 时，relay 未证实返回 `ACTION_RELAY_REQUIRED`，fresh 证明精确工具缺失返回
   `HOST_CAPABILITY_BLOCKED`，两者都保留 owner 并停止，不得降级。`$reconcile-codex-subagents` 只用于高级、
   脱敏、只读诊断。

上面的两个普通 `codex` 命令是 pre-Setup trust boundary。直接 native Mach-O、sanitized PATH、
显式 Git/Python 绑定、签名校验、cutover/rollback 以及隔离维护者验收属于强化高级内容；普通
用户只需按这三步操作。

| 依赖类别 | 项目 | Setup 行为 |
| --- | --- | --- |
| `bundled-with-guardian-plugin` | Guardian、Reconciler、Setup 三个 Skill | 随已安装 Guardian Plugin 提供 |
| `automatic-after-explicit-setup-apply` | All in Luna Plugin、固定 CLI wheel、14 个角色 | Plugin 保持 installed-but-unowned；一次明确授权的 `--apply` 后，只有 wheel、角色和受管 venv 可进入 receipt-backed ownership |
| `external-prerequisite` | macOS、Codex CLI、`portable-full` 的 Python `>=3.11`、提供可工作 `/usr/bin/git` 的 macOS Command Line Tools（或其他可信 provider）、GitHub HTTPS | 由受管事务外部提供并信任；Setup 只检测 provider，不安装它 |
| `detect-then-separately-authorize` | 包含生命周期 hooks/Node 的完整 Ponytail Plugin、Headroom、Node 集成和 HostAdapter relay | 仅检测；绝不静默安装、启用或信任 |
| `account-dependent` | Codex 登录、模型 entitlement/额度、私有 OMC | 需要新鲜账号/能力证据 |
| `none` | MCP（`mcp_servers: []`） | 不复制 MCP 配置、服务器或凭据 |

## 权威边界与交付流程

交付时显式调用 `$codex-workflow-guardian`。它会把用户的目标和计划依次带过
匹配运行的发现或恢复、精确 TaskGraph 指纹与共享主机槽位检查、受约束的作者路由、
compact/resume 恢复，以及分开的源码、测试和实时验收。动作和实时状态以原生 Codex
为权威。

All in Luna、OMC、Headroom 和 Ponytail 都是可选、必须探测能力的组件。All in Luna 是可选的
持久 TaskGraph 运行时；Guardian 是组合、权限和验收入口流程。可用时，All in Luna 管理匹配的持久
TaskGraph/Store/依赖/恢复/根完成；OMC 管理 Sol→Spark/Luna
作者路由和独立验证；Headroom 只观察传输；Ponytail `lite` 只属于作者。捆绑的 14 个 OMC
兼容角色模板是脱敏路由模板，不是完整/私有 OMC 安装、Skill、模型 entitlement 或额度。
能力缺失时按 owner 状态规则处理，绝不模拟组件。只有在不存在 durable run、最新 canonical lookup 证明没有
active matching run、且尚未签发 `HostAction` 时，才允许原生/手动降级。只要 durable run、active
matching run 或已签发 `HostAction` 已拥有该 scope，就保留其 owner 并停止；`ACTION_RELAY_REQUIRED`
与 `HOST_CAPABILITY_BLOCKED` 都是 owner/blocker 结果，不得降级。`resume` 必须读取保存的
`goal_ref`、`revision`、`intent_id`、`run_ref`；不得按 prompt 文本重新匹配或重新生成 `run_ref`。
Store/schema/digest 不一致是
`PROTOCOL_INTEGRITY_FAILURE`。本仓库不提供 MCP server、远程工具或服务，因为不存在远程
工具/服务边界。
Setup 安装的 Plugin 只是状态观察对象，不是 receipt-owned 资源：Codex CLI `0.146.0` 无法证明哪个
操作创建了 Plugin。因此 Plugin 删除始终是通过保留检查后、单独明确确认的原生 Codex 操作；旧
receipt 的 `owned_plugins` 字段会被忽略。All in Luna 继续保持 `installed-but-unowned`，直到单独确认删除。
先确认目标 Guardian selector 的精确 identity 与保留基线，再只移除精确的 Guardian selector；这一步不要求
marketplace 已经没有依赖。Guardian 移除后重新获取 fresh `codex plugin list --json`；只有确认没有剩余依赖才移除
marketplace。若仍有依赖，保留 marketplace 注册并停止。

Guardian 内置 `author-lite` 规则，不依赖 Ponytail 也能保持核心流程等价：分离需求/实现/验证角色，
使用 typed handoff，并保持独立证据。Ponytail 只是增强项。

默认 durable Store 是
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db`；每次 All in Luna
CLI 调用都必须用 `--db` 传入这个 canonical absolute 路径。先冻结保留目标语义但不含秘密的
`goal_identity`；将凭据、秘密、raw token 和 PII/客户标识替换为调用方提供的非秘密
`<credential-ref>`、`<artifact-ref>` 或 `<pii-ref>` 占位。绝不从受保护值派生占位，也不把原文写入
ID 或日志。确定性的 `intent_id` 是 `cwfg-` 加含 `protocol`、canonical workspace 和冻结
`goal_identity` 的 canonical JSON SHA-256 前 16 位小写十六进制；它是不透明的 21 字符 ID。
Store 可以保留授权范围内完整的 `RunIntent`，但其中的 `goal` 字段必须保持脱敏，受保护值只能
通过另行授权的 reference 传递。Store 中没有记录时只 start 一次；有一个匹配的 active 记录时只能
status/reconcile/resume；terminal、mismatch 或多个匹配时停止，并要求用户显式给出新 revision。
没有可信 Setup 目录和 managed CLI 时，绝不声称 durable run。

唯一允许 core-only 零匹配的证明是机器可读的
`host_capability.zero_match_evidence` 合同。精确的 identity registry 是
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json`：
目录必须是 `0700`，其中原子写入的 `guardian-run/v1` 条目必须是 `0600`，并且必须与
canonical Store 和精确的 `intent_id` 一致。runtime-available 证明要求安全的 canonical
路径、成功的精确 Store 查询、没有已签发的 `HostAction`，以及 Store/identity 结果一致；
首次成功 `start` 必须在任何 dispatch 前原子保存非秘密 identity。保存失败时保留新 run，
停止并标记 `recovery-required`；Store、sidecar、identity、schema 或 digest 不一致都是
`PROTOCOL_INTEGRITY_FAILURE`。

只有当 All in Luna 被 fresh 证明为 `unavailable`（不能是
`configured-unverified` 或 `blocked`），canonical Store 与 sidecar 以及精确 identity
entry 都被 fresh 证明不存在、且不跟随 symlink，没有当前 owner 或已签发 `HostAction`，
并且不存在已知 legacy/alternate Store 时，才可返回 `FRESH_ZERO_MATCH`，只允许一次受约束的
非 durable 原生/手动流程；不得声称 exactly-once 或 `resume`。Store/identity 已存在、
不安全、不可读、状态不一致，或已知 legacy/alternate Store 无法检查时，返回
`OWNER_LOOKUP_BLOCKED`，fallback 必须为 false；绝不扫描任意磁盘。

Reconciler 是高级、脱敏、只读的诊断工具；它绝不会 dispatch、close、archive、delete、repair、
publish、deploy 或修改生命周期目标。

外部发布、部署、破坏性操作和生命周期操作都必须获得精确的即时用户授权。源码/测试
通过不等于实时、浏览器或设备验收；这些结论需要单独的新鲜证据。

自然语言“全自动处理”或“coordinate this delivery”只是使用建议，不会自动授予
Setup、发布、部署或生命周期权限。只有精确调用 `$codex-workflow-guardian`、
`$reconcile-codex-subagents`、`$setup-codex-workflow-guardian` 才算 Skill 授权；三者均保持
`allow_implicit_invocation: false`。

## Profile 与证据边界

| Profile | 验收边界 |
| --- | --- |
| `core` | Guardian Plugin 加 Guardian、Reconciler、Setup 三个 Skill。 |
| `portable-full` | `core` 加固定版本 All in Luna Plugin/CLI wheel 和 14 个角色；Python `>=3.11`。Setup 不安装 Ponytail，也不启用或信任 hooks；`--apply` 只到 installed，HostAdapter 可调用能力须在安装后用新任务验收。 |
| `machine-integration` | Desktop/CLI skew；Headroom 存在/版本/健康/路由；生命周期 hooks 需要时的用户 Node；hooks 与本机启动。需要另行授权和实时验收。 |
| `account-dependent` | Codex 登录、模型 entitlement/额度、私有 OMC Skill。需要新鲜 receipt。GitHub 网络访问是单独的外部前置条件，不证明 Codex 账号能力。 |

源码/测试、已安装状态、可调用能力、行为/实时证据必须分开。绿色测试、安装 receipt
或角色模板都不能证明模型访问或当前浏览器/日志/DB/设备行为。portable-full 的目标是
功能层面的公开等价，不是复制本机脏的 RC2/OMC/Headroom 状态。本项目目标是 GitHub-backed Codex
repo marketplace source，不是 universal public Plugins Directory 的收录声明。

不要使用单独的 `ready` 标签。只有目标文件和匹配 receipt 都存在时才报告
`installation-ready`；存在配置但没有新鲜可调用能力 receipt 时报告
`capability-configured-unverified`；只有声明的安装验收通过后才报告
`acceptance-installed`。这些状态都不能证明当前实时行为。

## 依赖矩阵

| 组件 | 要求或已验证基线 | 边界 |
| --- | --- | --- |
| Codex Desktop/CLI | 最低 `0.146.0`；本机 CLI 已验证 `0.146.0`（Desktop 内嵌版本可能不同） | 原生 Codex 是动作和权限权威；`plugin list --json`、marketplace 和 `plugin add --json` 的 JSON 形状只在 CLI `0.146.0` 测试，schema 不符时安全失败。 |
| Python | 捆绑脚本和 Setup Skill 使用 `3.9`；托管 All in Luna CLI 使用 `>=3.11` | 不需要字节码或可变依赖解析器 |
| All in Luna | `2.0.0-rc.3`，commit `723088a7c0d7342f077ad675c6ea72d7e3996536`，Apache-2.0；wheel SHA-256 `e2e59ce76deab1b39efe6feb1b64c983101c313268ce52dfa25787b77295dee1` | 可选持久运行时；仅接受精确 wheel，不运行依赖解析器 |
| Ponytail | `4.9.0`，commit `2ed6c52c9d7e5e56942508591085fd45dea277d3`，MIT | Ponytail Skills 本身不需要 Node；生命周期 hooks 可能需要用户提供的 Node。Setup 不安装、启用或信任 hooks，machine integration 另行验证 Node。 |
| OMC 兼容角色 | 14 个脱敏模板（不是完整/私有 OMC）：`code-reviewer`、`coder`、`debugger`、`document-specialist`、`executor`、`explore`、`luna-coder`、`luna-worker`、`security-reviewer`、`sol-lead`、`spark-verifier`、`test-engineer`、`tracer`、`verifier` | 在新鲜 role/model/reasoning receipt 之前，模型 entitlement 为 `configured-unverified` |
| Headroom | `headroom-ai` `0.34.0`；canonical 仓库 [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom)，Apache-2.0，Python `>=3.10` | 仅探测/手动集成。制品哈希和传递依赖哈希未锁定；存在/版本不证明 readiness、健康、路由、启动、初始化或信任。 |
| Node.js | `machine-integration` 所需的用户提供 `node` 运行时 | Ponytail Skills 不需要 Node，只有生命周期 hooks 可能需要；Setup 永不自动安装或信任 hooks。 |
| Xcode Command Line Tools | `portable-full` 要求为固定 `/usr/bin/git` 提供可工作的 provider | 外部前提；Setup 只检测，不安装。 |
| HostAdapter / host relay (`host_capability`) | Codex Desktop 的确切顶层 relay `codex_app__create_thread`，或 CLI/IDE 的 native `lane-direct` | Setup 不安装。只有在尚无 durable run/`HostAction` 且完成 fresh canonical zero-match 证明后，才允许受约束的原生/手动降级。已有 owner 且 relay 未证实时返回 `ACTION_RELAY_REQUIRED`、保留 owner 并停止；fresh 证明精确工具缺失时返回 `HOST_CAPABILITY_BLOCKED`、保留 owner 并停止；两种 owner 状态都不得降级。 |
| GitHub 网络 | 访问 GitHub-backed Codex repo marketplace source 和固定制品主机的 HTTPS 能力 | 仅表示连通性；与 Codex 登录、模型 entitlement/额度、私有 OMC 访问分开。 |
| MCP 及其他远程服务 | 不需要；`mcp_servers` 为 `[]` | 绝不复制主机 MCP 配置、服务器注册或凭据。Memos、Context7 等外部服务明确排除。 |

## macOS 高级安装与维护者验收

安装严格分两步并使用不可变引用。Setup 的目标是 `portable-full`；Guardian
Plugin 本身已经提供 `core`。普通安装应使用你明确选择的持久 Codex home；不要把临时
home 当作普通安装，也不要以为它会自动出现在正常任务中。

### 强化维护者持久化安装

定义一个现有的持久 home、Codex 可执行文件、Git 可执行文件、可信 Python 可执行文件和确切的
已审计 40 位提交，然后把 Guardian Plugin 安装到同一个目标。`portable-full` 要求
`PYTHON_BIN >=3.11` 且具备 `venv` 和 `ensurepip`。可使用可信的当前解释器、显式传入的解释器，
或仅使用固定 macOS 候选：`/opt/homebrew/bin/python3.14` 至 `python3.11`，再到
`/usr/local/bin/python3.14` 至 `python3.11`。Setup 的自动选择只探测这些固定路径，绝不执行
ambient PATH 中任意的 Python 启动器。

执行文件信任必须显式绑定：将 `GUARDIAN_CODEX_BIN`、`GUARDIAN_GIT_BIN` 和
`PYTHON_BIN` 设置为 canonical absolute 路径，不通过 ambient `PATH` 查找。
只有通过 OpenAI Developer ID 签名验证的 native Codex 才能执行。Homebrew
JS launcher 只允许作为受约束的静态定位器，用来定位 versioned bundled
native Codex；Setup 不执行 Node 或 JavaScript wrapper。信任边界是 root 和
当前 UID；只有标准 Homebrew 前缀在无 `extended ACL` 时允许 `admin group-write`。
这不防御 same-UID、root 或受信任 admin 的攻击者。

普通已有 `CODEX_HOME` 不需要为了 Setup 执行 chmod 或迁移。只要归当前 UID 所有、group/other
不可写且没有 extended ACL，`0700`、`0750` 或 `0755` 都可接受。Setup 会把敏感的
`workflow-guardian` 目录保护为 `0700`，receipt、journal 和 sidecar 文件保护为 `0600`；维护者
隔离验收另用独立的 `0700` 临时 home。

磁盘 receipt 是 `private-local`。stdout 只提供 `public-redacted` projection，
排除 path、hash、device/inode、env 和 raw logs。失败时保留 receipt、journal
和 quarantine entry；调查期间不要覆盖它们。
stdout 合同为 `codex-workflow-guardian/bootstrap-stdout/v1`：只保留
`schema`、`projection`、`generation`、`mode`、`platform`、`status`、
`canonical_source_verified`、`guardian_ref_verified`、`existing_receipt`、
`installation_status`、`capability_status`、`acceptance_level`、不含敏感信息的
`planned` count/components/action、`conflict_summary` count/categories、component
name/status/version 摘要、rollback action 摘要、notes、conflicts 的 name/reason pairs、
failure 和 recovery。它排除绝对/相对路径、`*_relative`、SHA/hash/digest/commit/selector、
device/inode、ownership/provenance、environment values、credentials 和 raw logs。
在所有 mode 中，`existing_receipt` 都表示本次调用开始时的观察：`present` 表示 Setup
在调用开始前成功验证了先前的 private receipt pair；它不表示成功 `--uninstall` 后该 pair
仍然存在。`absent` 表示调用开始时没有可验证的先前 private receipt pair。
`acceptance_level` 只能是 `preflight|installed|uninstalled|transaction-recovery`；在新任务返回真实
receipt 前，`capability_status` 始终为 `configured-unverified`。`planned` 只包含不泄露信息的
All in Luna Plugin/CLI/venv 与 14 个角色的 count/action 摘要，绝不包含路径或哈希。

先添加 Git-backed 仓库 marketplace 并保存 JSON；它不是 Universal Public Plugins Directory 的安装。
`GUARDIAN_CODEX_BIN` 必须直接指向供应商签名的 native Mach-O Codex。不要把
`/opt/homebrew/bin/codex` 或 `/usr/local/bin/codex` 当作原生二进制；它们可能是
Node/JavaScript wrapper，而下面的受限 PATH 刻意排除了 Homebrew。请从可信安装中解析
Apple Silicon 或 Intel 对应的供应商二进制，再执行以下验证：

```sh
GUARDIAN_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"  # 先确认这个持久目标
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"  # 替换成已审计的 40 位十六进制提交
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

停止。打开 `$MARKETPLACE_ADD_JSON`，人工验证 JSON、marketplace 名称/来源、精确 ref，且没有意外注册。
任何字段缺失或异常都停止，不得继续。

只有在该人工闸门通过后，添加 Guardian Plugin 并保存 JSON：

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。打开 `$PLUGIN_ADD_JSON`，人工验证 JSON schema、精确的 `pluginId`、预期的
`name` 和 `marketplaceName`、非空 `version`、`authPolicy` 以及非空
`installedPath`。任一字段缺失或异常都停止，不得猜测路径。只有检查通过后，复制该路径并验证安装包：

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

停止。确认 bootstrap、lock、marketplace 和全部 14 个 role asset 均来自刚检查的 Plugin 根目录。
开始新的 Codex 任务，从该已安装 Plugin 显式调用 `$setup-codex-workflow-guardian`；不得从 source
checkout 派生 apply 路径。分开的步骤中先探测 Python 能力并运行只读 `--check`：

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

停止。打开并审查 `$CHECK_RECEIPT_JSON` 的 public-redacted stdout projection，确认非敏感的
调用开始时 `existing_receipt`：`absent` 表示没有可验证的先前 private receipt，只审查 public logical plan；
`present` 才检查从之前成功 `--apply` 验证出的未改变固定 receipt pair。只有 provenance、schema、
能力、冲突和计划所有路径均清楚后，才能单独请求 `--apply`。非零 `--check` 绝不是 apply 授权：

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

另行审查 `$APPLY_RECEIPT_JSON` 这个 public-redacted stdout projection 与 private-local live receipt
pair。上述命令写入选定 home，不修改本仓库 checkout。失败时保留 receipt、transaction journal 和
quarantine entry，不要覆盖。

Guardian、Reconciler、Setup 三个 Skill 都要求显式调用，并设置
`allow_implicit_invocation: false`。marketplace 和 Plugin 命令只会写入选定的 home，不会
修改本仓库 checkout。

### 普通卸载

开始新的 Codex 任务，显式调用 `$setup-codex-workflow-guardian`，并要求它执行安全卸载。Skill 会自行
发现并校验确切的已安装 Plugin 根目录/ref，然后只删除已证明由匹配 receipt 拥有且未改变的角色文件和
受管 All in Luna venv。它绝不把 Plugin 当作 receipt-owned：Setup 添加的所有 Plugin（包括 All in Luna）
均保持 `installed-but-unowned`，旧 `owned_plugins` 字段不能授权删除。完成保留检查后，先确认目标 Guardian selector
的精确 identity 与保留的非 Guardian 基线，然后只移除精确的 Guardian selector；这一步不要求 marketplace 已经没有依赖。
该 selector 仍须作为单独、明确确认的原生 Codex 操作移除。All in Luna 继续保持
`installed-but-unowned`，直到单独确认删除。Guardian 移除后重新获取 fresh `codex plugin list --json`；只有确认没有剩余依赖才移除
marketplace。若仍有依赖，保留 marketplace 注册并停止。遇到冲突或无法解释的保留状态即停止，
保留 receipt、journal 和 quarantine 证据。

`plugin marketplace list --json` 单独只能证明注册存在。

下面的长 shell 序列只供维护者执行隔离恢复或审计切换，不是普通用户路径。

### 维护者/恢复卸载（高级）

使用 `codex plugin add --json` 返回的确切已安装 Plugin 根目录；绝不从 source checkout 推导路径。Setup 卸载只对未改变且由 receipt 证明拥有的角色文件和 venv 生效。Setup 始终保留 Plugin selector；完成下面的保留检查后，Plugin 才能通过单独、明确确认的 Codex 操作移除。任何步骤失败都保留 receipt、journal 和 quarantine 证据。

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"
PLUGIN_INSTALLED_PATH="/absolute/path/from-plugin-add-installedPath"
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-plugin-add-installedPath" || exit 1
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
UNINSTALL_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.uninstall.XXXXXX")"
test -n "$UNINSTALL_RECEIPT_JSON" && test -f "$UNINSTALL_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --guardian-ref "$GUARDIAN_REF" >"$UNINSTALL_RECEIPT_JSON"; then
  echo "uninstall failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。检查 `$UNINSTALL_RECEIPT_JSON` 以及私有 receipt/journal。出现冲突、部分结果、所有权变化或无法解释的保留状态都停止；receipt-owned 卸载不授权删除无关内容。

```sh
POST_UNINSTALL_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-uninstall-plugin-list.XXXXXX")"
test -n "$POST_UNINSTALL_PLUGIN_LIST_JSON" && test -f "$POST_UNINSTALL_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_UNINSTALL_PLUGIN_LIST_JSON"; then
  echo "post-uninstall plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。要求 JSON 有效，确认精确 Guardian selector 仍在，并确认所有保留的非 Guardian 行及其来源身份没有变化；然后只移除精确的 Guardian selector。
这一步不要求 marketplace 已经没有依赖。

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin remove codex-workflow-guardian@onebigmoon-codex-workflows; then
  echo "Guardian selector removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。确认精确 Guardian selector 已移除；任何其他 selector 发生变化都停止。

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。要求 Guardian 移除后的 fresh JSON 有效；只有确认没有剩余依赖才移除 marketplace。若仍有已安装 selector 引用
`onebigmoon-codex-workflows`，保留 marketplace 注册并停止。下面的 marketplace list 只能证明注册存在。

```sh
MARKETPLACE_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace-list.XXXXXX")"
test -n "$MARKETPLACE_LIST_JSON" && test -f "$MARKETPLACE_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace list --json >"$MARKETPLACE_LIST_JSON"; then
  echo "marketplace list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。只有有效 JSON 证明没有已安装 Plugin 或保留依赖仍使用 `onebigmoon-codex-workflows` 时，才移除该 marketplace；否则保留注册并停止。

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace remove onebigmoon-codex-workflows; then
  echo "marketplace removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。确认只移除了精确 marketplace 注册。任何失败都保留 `$UNINSTALL_RECEIPT_JSON`、私有 receipt、transaction journal 和 quarantine entry。

### 维护者隔离验收

只有发布验收才使用全新的临时 home；它与普通安装分开，不会自动改变用户的正常 Codex
任务。维护者验收使用同一 Python 边界和 3.11–3.14 固定候选。必须先安装确切 Plugin，再从成功的
`codex plugin add --json` 的 `installedPath`（Plugin 根目录）派生 `SETUP_SKILL_DIR`。维护者 checkout
只有在确切已审计提交、干净 tracked checkout、完整文件/哈希校验、canonical root、root/current UID
所有权、无 world-writable 或未授权 group-writable 权限且无 extended ACL 时，才可用于只读校验；
bootstrap、doctor、postflight 必须来自同一可信 root。普通 Setup 和 canonical `--apply` 必须绑定已安装
marketplace Plugin 的 bootstrap、lock、marketplace 和 14 个 role asset，绝不凭空写路径：

```sh
GUARDIAN_CODEX_HOME="$(/usr/bin/mktemp -d "/tmp/codex-workflow-guardian.XXXXXX")"
test -n "$GUARDIAN_CODEX_HOME" && test -d "$GUARDIAN_CODEX_HOME" || exit 1
GUARDIAN_CODEX_HOME="$(cd "$GUARDIAN_CODEX_HOME" && pwd -P)" # 规范化 macOS 路径
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="42db0a2c493b39bbf7f1661c2cc375f9b51af769"  # 确切的已审计 40 位提交
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

停止。打开 `$MARKETPLACE_ADD_JSON`，人工验证 JSON、来源、名称和不可变 ref；失败或异常即停。

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。检查 JSON schema、精确的 `pluginId`、预期的 `name` 和 `marketplaceName`、非空
`version`、`authPolicy` 以及非空 `installedPath`；任一字段缺失或异常都停止。

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

停止。确认 bootstrap、lock、marketplace 和全部 14 个 role asset。接着开始新 Codex 任务并显式调用
`$setup-codex-workflow-guardian`；不得从 source checkout 派生 apply 路径。单独运行能力探测和 check：

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

停止。将 `$CHECK_RECEIPT_JSON` 作为 public-redacted stdout projection 审查并确认
调用开始时的 `existing_receipt`：`absent` 时只审查 public logical plan；`present` 时才检查从之前成功
`--apply` 验证出的固定 receipt pair。再审查 provenance、schema、能力、冲突和所有权；非零 check 不是 apply 授权：

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

审查 `$APPLY_RECEIPT_JSON` 与 live receipt pair。marketplace/Plugin 命令只写入选定的 `GUARDIAN_CODEX_HOME`，
不修改本仓库 checkout。安装后开始新的 Codex 任务。Guardian、Reconciler、Setup 都要求显式调用，
并保持 `allow_implicit_invocation: false`。

`--guardian-ref` 必须是确切的 40 位十六进制提交。`--check` 自身不直接写入受管内容，但会
执行经过校验的 Codex/Python 探测，外部程序可能维护自身状态；发布验收要在全新隔离目标上
比较树、mtime 和哈希。它会在需要变更、发生冲突或缺少必需能力时返回非零；审查 receipt
后才运行 `--apply`。CLI 只有 `--check`、`--apply`、`--uninstall`。`--uninstall` 只删除由匹配 receipt
证明拥有且仍未修改的角色文件和托管 venv，绝不删除 Plugin selector。Setup 添加的每个 Plugin（包括
All in Luna）均为 installed-but-unowned，旧 `owned_plugins` 字段会被忽略。预先存在或已修改的受管内容
会保留并报告冲突。
Python `>=3.11` 可用时，receipt 会暴露托管 All in Luna 可执行文件
在显式目标的 `venvs/allinluna/bin/allinluna`。Ponytail hooks 永不自动启用或信任；如需使用，
必须先审查并显式启用。

Setup 后开始新的 Codex 任务，并要求 role、model、reasoning receipt，之后才能把 OMC 兼容
lane 当作已配置。模板本身不能证明模型访问、Headroom 路由、hook 激活或 All in Luna 持久完成。

切换不可变 ref 时，必须把同名 marketplace 注册视为该 ref 的一部分。当前 Codex CLI 的
`plugin marketplace upgrade` 没有 `--ref` 选项，因此先用 `plugin list --json` 捕获完整列表并派生保留基线：
每个已安装 Plugin 行（`pluginId`、installed 状态和来源身份）都必须保留；Codex CLI 0.146.0 没有
Plugin 创建者/变更证明，不得排除任何 selector 作为 receipt-owned。然后完成只针对角色文件和 venv 的
旧 receipt 卸载，再单独确认移除 Guardian Plugin 和同名 marketplace 注册，最后用
`--ref "$NEW_GUARDIAN_REF"` 重新添加同一注册。这次注册切换不授权移除其他已安装 Plugin。
注册空窗期间，保留的 Plugin 可能暂时不可发现；marketplace 重新添加后必须运行
`plugin list --json` 并与基线比较。JSON 无效、任一基线行缺失/不再 installed 或来源意外变化，
都必须停止。

切换前，先捕获/归档旧的 live
`workflow-guardian/bootstrap-receipt.json` 和 `workflow-guardian/bootstrap-receipt.sha256` 作为审计证据，
并保留旧 Plugin 根目录及其派生 Setup 路径。旧 `--uninstall` 成功后 live receipt pair 会被消耗，不能
复用或手动恢复归档 pair。Setup uninstall 始终保留所有 Plugin，包括由本次 Setup 添加的 All in Luna；
Plugin 只能在保留检查后通过单独确认的 Codex 操作移除。每个破坏性步骤前都有独立 JSON 证明和人工停止；注册空窗期间不得把暂时
不可发现误认为删除。下面的前向切换不会自动回滚；Plugin 始终要在保留检查后通过单独确认的 Codex
操作删除：

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PLUGIN_BASELINE_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin-baseline.XXXXXX")"
test -n "$PLUGIN_BASELINE_JSON" && test -f "$PLUGIN_BASELINE_JSON" || exit 1
OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-old-plugin-add-installedPath"
OLD_SETUP_SKILL_DIR="$OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
OLD_GUARDIAN_REF="OLD_AUDITED_COMMIT_SHA"  # 旧版本确切 40 位十六进制提交
NEW_GUARDIAN_REF="NEW_AUDITED_COMMIT_SHA"  # 新版本确切 40 位十六进制提交
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json > "$PLUGIN_BASELINE_JSON"
```

停止。检查完整基线与旧 receipt；所有已安装 Plugin 行（含 All in Luna）都必须保留，因为 Codex CLI
没有 Plugin 创建者/变更证明，并归档旧 receipt pair。

```sh
"$PYTHON_BIN" -I -B "$OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF"
```

停止。审查旧 uninstall receipt，确认仅移除 receipt-owned 角色文件和 venv；所有 Plugin 都必须保留。
冲突或部分失败即停。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。确认精确 Guardian selector 仍在且保留基线未变；然后只移除精确的 Guardian selector。这一步不要求 marketplace 已经没有依赖：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

停止。确认 Guardian selector 已移除后，先捕获 Guardian 移除后的 fresh Plugin list；只有确认没有剩余依赖才移除 marketplace。
若仍有已安装 selector 引用 `onebigmoon-codex-workflows`，保留 marketplace 并停止。下面的 marketplace list 只能证明注册存在：

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

停止。只有精确注册存在且 JSON schema 有效时，才可移除它：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

停止。确认注册已移除，再添加新 ref 的 Git-backed marketplace 并保存 JSON：

```sh
MARKETPLACE_READD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_READD_JSON" && test -f "$MARKETPLACE_READD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$NEW_GUARDIAN_REF" --json >"$MARKETPLACE_READD_JSON"; then
  echo "new marketplace add failed; stop" >&2
  exit 1
fi
```

停止。检查 `$MARKETPLACE_READD_JSON` 的 schema、canonical 来源/名称和新 ref：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。对照保留基线；所有行（含 All in Luna）都须保持 installed 和来源不变；不能因 Setup uninstall
而缺失任何 Plugin。然后添加新 Guardian Plugin 并保存 JSON：

```sh
NEW_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$NEW_PLUGIN_ADD_JSON" && test -f "$NEW_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$NEW_PLUGIN_ADD_JSON"; then
  echo "new Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。检查 `$NEW_PLUGIN_ADD_JSON` 的 schema、预期的 `pluginId`、`name`、`marketplaceName`、
`version`、`authPolicy` 和非空 `installedPath`，再验证新包：

```sh
NEW_PLUGIN_INSTALLED_PATH="/absolute/path/from-new-plugin-add-installedPath"
NEW_SETUP_SKILL_DIR="$NEW_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$NEW_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-new-plugin-add-installedPath" || exit 1
test -f "$NEW_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$NEW_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

停止。确认 bootstrap、lock、marketplace 和全部 14 个 role asset。单独运行 `--check`：

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

停止。审查 `$CHECK_RECEIPT_JSON`；只有明确授权后才可单独 apply：

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "new Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

审查 `$APPLY_RECEIPT_JSON` 与 live receipt pair 后才可宣称切换完成。

### 前向切换失败后的人工回滚

只有前向切换失败后才能运行下面的回滚 snippets；它们不是成功前向流程的继续执行。使用上面相同的
显式变量。归档的旧 receipt 仅用于审计证据，不能手动恢复。每个破坏性回滚步骤都必须有前置 JSON
检查和人工停止。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。若新 Guardian selector 存在且新 receipt 可用，确认 Setup 只会卸载新 receipt-owned 角色文件和 venv；
Plugin selector 没有资格被卸载，必须单独人工检查。

```sh
"$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF"
```

停止。审查新 uninstall receipt；冲突、部分失败或所有权异常即停，并确认 Setup 没有移除任何 Plugin selector。
只有 marketplace 注册为空的同一切换窗口内，保留 Plugin 才可能暂时不可发现；这不是删除证据，也不能放宽删除检查。
再次检查 Plugin 列表：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。确认保留基线完整；只有精确 Guardian selector 仍存在时，才只移除精确的 Guardian selector；这一步不要求 marketplace 已经没有依赖：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

停止。确认 Guardian selector 移除结果，先捕获 Guardian 移除后的 fresh Plugin list；只有确认没有剩余依赖才移除 marketplace。
若仍有已安装 selector 引用 `onebigmoon-codex-workflows`，保留 marketplace 并停止。下面的 marketplace list 只能证明注册存在：

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

停止。仅当精确注册存在且 schema 有效时移除该注册：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

停止。确认注册已移除，再重新添加旧 Git-backed marketplace 并保存 JSON：

```sh
RESTORED_MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$RESTORED_MARKETPLACE_ADD_JSON" && test -f "$RESTORED_MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$OLD_GUARDIAN_REF" --json >"$RESTORED_MARKETPLACE_ADD_JSON"; then
  echo "old marketplace re-add failed; stop" >&2
  exit 1
fi
```

停止。检查 `$RESTORED_MARKETPLACE_ADD_JSON` 的 schema、canonical 来源/名称和旧 ref，再对照保留基线：

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。确认所有保留非 Guardian 行（含 All in Luna）仍 installed 且来源正确，再重新添加 Guardian Plugin：

```sh
RESTORED_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$RESTORED_PLUGIN_ADD_JSON" && test -f "$RESTORED_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$RESTORED_PLUGIN_ADD_JSON"; then
  echo "old Guardian plugin re-add failed; stop" >&2
  exit 1
fi
```

停止。检查 `$RESTORED_PLUGIN_ADD_JSON` 的 schema、预期的 `pluginId`、`name`、`marketplaceName`、
`version`、`authPolicy` 和非空 `installedPath`。
绝不复用切换前的 `OLD_PLUGIN_INSTALLED_PATH`；复制新路径并派生恢复路径，验证恢复后的安装包：

```sh
RESTORED_OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-restored-old-plugin-add-installedPath"
RESTORED_OLD_SETUP_SKILL_DIR="$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$RESTORED_OLD_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-restored-old-plugin-add-installedPath" || exit 1
test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

停止。确认恢复后的 bootstrap、lock、marketplace 和 14 个 role asset。运行旧 Setup 的 check 并审查：

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

停止。审查 `$CHECK_RECEIPT_JSON`；非零 check 不是 apply 授权。只有明确请求后才运行独立 apply：

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "restored old Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

审查新的 restored receipt pair；不要恢复归档 pair、移除其他 Plugin 或手动删除 receipt-owned 角色/venv 状态之外的路径。

### 独立安装三个 Skill 的替代方案（仅限确切的已审计提交）

如果不需要 Plugin，`$skill-installer` 或直接安装脚本必须把 Guardian、Reconciler、Setup
解析到同一个确切的 `42db0a2c493b39bbf7f1661c2cc375f9b51af769`；绝不要使用 `main`：

```sh
SKILL_INSTALLER="/path/to/install-skill-from-github.py"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$SKILL_INSTALLER" \
  --repo OneBigMoon/codex-subagent-reconciler \
  --ref 42db0a2c493b39bbf7f1661c2cc375f9b51af769 \
  --path skills/codex-workflow-guardian skills/reconcile-codex-subagents \
         skills/setup-codex-workflow-guardian
```

该回退方案只是 `skill-only`，并未达到 `core`：它会在安装器选定的目标下写入三个 Skill
目录，但不会创建已安装 Guardian Plugin 或其 git marketplace provenance。不要从此路径
声称 `core` 或 `portable-full`。独立 Setup Skill 仅作为文档/诊断入口；在 canonical 已安装
Guardian Plugin 和匹配 git provenance 存在前，`--apply` 会以 `configured-unverified`
fail closed。

## Guardian provenance

portable-full 必须同时证明：canonical GitHub 仓库
`https://github.com/OneBigMoon/codex-subagent-reconciler`；用户审计的确切 40 位
`GUARDIAN_REF`；GitHub-backed Codex repo marketplace source resolver 返回唯一已安装
Guardian；canonical git marketplace URL/root；以及 checkout 的 Git HEAD 等于该 ref。lock 与
marketplace 必须在仓库、路径、ref、制品哈希上逐项一致。仓库内 local marketplace 只用于开发/测试，
不是 universal public Plugins Directory 的发布声明；resolver 无法证明 canonical provenance 时，
portable-full `--apply` 必须以 `configured-unverified` 安全失败。receipt 记录操作和所有权，不是
发布者身份、授权或签名证明。

## 只读校准器

仅向 `$reconcile-codex-subagents` 提供合成或脱敏 JSON。doctor 只读取一个明确指定的
普通文件；postflight 只读取一个明确的 before 文件和一个明确的 after 文件。v1 Schema
继续用于诊断；严格 v2 运行检查使用调用方生成的
`scope` 和稳定 `record_key`，它们只是关联键，绝不是可操作的生命周期目标。
`v1` 保留诊断兼容：未知事件会报告为 `unsupported-event` 并以 `2` 退出；严格
`v2` 在 schema validation 阶段拒绝未知事件类型。

```sh
RECONCILER_SKILL_DIR="/path/to/reconcile-codex-subagents"  # 包含本 Skill.md 的目录
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/doctor.py" \
  --input snapshot.json --json
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/postflight.py" \
  --before before-v2.json --after after-v2.json \
  --record-key record-1 --json
```

将 `RECONCILER_SKILL_DIR` 只设置为已安装 Guardian Plugin 的 `codex plugin add --json` 返回的
`installedPath`。使用前验证 plugin manifest、不可变 ref、canonical marketplace provenance 以及完整
lock/role asset；未经验证的 checkout 不是普通运行根目录。维护者只读校验还必须使用确切已审计提交的
干净 tracked checkout，完成文件/哈希、所有权、权限和 ACL 校验，并让 doctor/postflight 来自同一 root。
这些命令不依赖仓库当前工作目录。

退出码 `0` 表示一致/通过，`1` 表示不匹配/失败，`2` 表示无效、歧义、无法判定或不
支持。明确不匹配的 before 可以作为修复基线，但 after 必须一致，且选定记录必须发生
真实且序列向前的转换。每条已解析记录还必须逐条对应（`running` 及声明的
`live_state: running` 对应 UI `active`，`terminal` 及声明的 `live_state: terminal` 对应
UI `done`）；相反的行不能在聚合计数中相互抵消。v2 同时校验派生的 live 计数和独立
计算的可见 UI 行计数，聚合计数不能掩盖陈旧的选定行。未变化的终态记录、scope 或 key
漂移、旧 epoch、更新的 `not_found`、未知字段、重复键、控制字符、不安全标识符和
不安全文件都会安全失败。

报告会替换标识符，但这不是匿名化。快照输出以及所有 v1/v2 JSON 或文本报告都带有
`trust: unauthenticated-consistency-evidence`；它们绝不是授权或协议真相。公开分类使用
snapshot-claim 标签。捆绑脚本只使用标准库，有界、不联网、不轮询、不写文件，绝不会
关闭、中断、归档、删除、修复、部署或以其他方式修改生命周期目标。

## 验收级别

Profile 和证据集合必须分开：

- **`core`：** Guardian Plugin 加 Guardian、Reconciler、Setup 三个 Skill。
- **`portable-full`：** `core` 加固定版本 All in Luna Plugin/CLI wheel 和
  14 个角色；Python `>=3.11`。Setup 不安装 Ponytail，也不启用或信任 hooks；两者都需要单独的
  machine-integration 授权与实时验收。
- **`machine-integration`：** Desktop/CLI skew、Headroom 存在/版本/健康/路由、生命周期 hooks 需要时的用户 Node、本机启动；需要另行授权和实时验收。Ponytail Skills 本身不需要 Node，hooks 永不自动信任。
- **`account-dependent`：** Codex 登录、模型 entitlement/额度、私有 OMC Skill；需要新鲜 receipt。GitHub 网络访问是单独外部前置条件，不证明这些账号能力。

源码/测试（仓库及 validator 结果）、已安装状态（resolver、lock、哈希、路径、receipt）、
可调用能力（Skill 和角色模板）以及行为/实时（新任务和浏览器/日志/DB/设备证据）是不同
证据。可复现 Git 交付也要单独记录。成功标准是功能层面的公开等价，不是逐字节复制本机脏的
RC2/OMC/Headroom 状态。

Receipt 和 journal 的 SHA sidecar 只能发现损坏或 schema 漂移，不能认证记录，也不能防止拥有
`CODEX_HOME` 写权限的同 UID 攻击者同时改写记录和 sidecar。Setup 只接受固定的受管路径/Plugin
allowlist，并在所有权敏感操作前后重新观察实时状态。

## 验收与安全

运行两套 unit suite、三个 Skill quick validator、一个 Plugin validator、三个脚本 `--help`、
JSON/TOML/README 常量检查、无 bytecode 检查和 `git diff --check`。官方本地 validator 在
无网络且没有全局 Skill 路径的 CI runner 上可能不可用；此时它们是明确的 release gate，
不能声称 CI 已执行。这些检查只建立源码/测试证据，不代表已经部署或当前实时可用。精确
Schema 见 [`skills/reconcile-codex-subagents/SKILL.md`](skills/reconcile-codex-subagents/SKILL.md)，
安全边界见 [`SECURITY.md`](SECURITY.md)。
