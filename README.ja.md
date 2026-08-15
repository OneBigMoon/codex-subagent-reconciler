# Codex Workflow Guardian

`codex-workflow-guardian` は macOS 専用の Codex Plugin/Skill ワークフローであり、MCP
サーバーではありません。ライブのリモートツール、データ、認証境界を持たないためです。
Guardian のオーケストレーション、厳格な読み取り専用の
`reconcile-codex-subagents` Reconciler、明示的に呼び出す
`setup-codex-workflow-guardian` Setup Skill を組み合わせます。旧リポジトリ slug は
`codex-subagent-reconciler` です。

これは非公式のコミュニティプロジェクトであり、OpenAI 公式製品ではありません。

言語: [English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) |
[Español](README.es.md)

対応プラットフォームは macOS のみです。core Plugin のインストールには Plugin marketplace
対応の Codex CLI だけが必要で、Python は不要です。バンドルスクリプトの実行には Python
3.9+、`portable-full --apply` には Python 3.11+ が必要です。
manifest には OS インストールゲートがないため、別の OS が Plugin を発見またはインストールする
場合がありますが、それはサポートを意味しません。バンドルされた実行可能 Skill は非 Darwin ホスト
では snapshot の読み込みや管理対象の読み書きより前に fail closed します。サポート対象は macOS のみです。

## 三段階の考え方と最短ルート

1. ユーザーがすでにインストールして信頼している通常の `codex` コマンドを pre-Setup trust
   boundary として使います。監査済みの不変 SHA で GitHub-backed marketplace を登録し、Guardian
   Plugin をインストールします。これで三つの Guardian Skill が同梱されますが、認証情報、MCP 設定、
   private OMC はコピーしません。

   ```sh
   GUARDIAN_REF="AUDITED_COMMIT_SHA"
   codex plugin marketplace add OneBigMoon/codex-subagent-reconciler \
     --ref "$GUARDIAN_REF" --json
   codex plugin add codex-workflow-guardian@onebigmoon-codex-workflows --json
   ```

2. 新しい Codex タスクを開始し、`$setup-codex-workflow-guardian` を明示的に呼び出します。
   Skill はまず自身同梱の `installed` ルート `E` を確認し、clean な immutable
   かつ exact-ref が一致することを検証します。次に `codex plugin list --json` の唯一の
   Guardian selector を `P` として取得し、`P` が canonical marketplace provenance であること、
   さらに `P` と `E` の exact-ref が一致することを確認してから Plugin JSON と
   同梱資産を読み取り検証します。固定 macOS 候補から Python を検出し、必要なら対応する
   Codex wrapper を静的に解決します。JSON の手動解析や native/Python path の手入力は不要です。
   public `--check` projection を確認してから、その一回の
   `--apply` に都度許可を与えます。apply は固定 All in Luna Plugin、正確な CLI wheel、14 個の
   サニタイズ済みロールテンプレートを選択した永続 `CODEX_HOME` に自動インストールします。
   Ponytail、Headroom、Node、ライフサイクル hook、HostAdapter relay は任意の別認可です。
   Setup が追加した Plugin（All in Luna を含む）は `installed-but-unowned` のままです。receipt ownership
   の対象は、Setup が管理したことを証明できるロールファイルと venv だけです。
   CLI `0.146.0` の `plugin list --json` 行は `installedPath` を含まない場合があります。パス
   回復のための glob/scan、推測、再実行 `plugin add` は行いません。
3. 以後の各デリバリーでは `$codex-workflow-guardian` だけを呼び出します。All in Luna の
   能力が検証済みなら一致する永続実行を再利用します。owner がまだなく fresh canonical zero-match
   の証明が完了した場合だけ、制限されたネイティブ/手動フォールバックを許可します。owner があれば、
   relay 未検証は `ACTION_RELAY_REQUIRED`、fresh に正確な tool がない場合は `HOST_CAPABILITY_BLOCKED` とし、
   どちらも owner を保持して停止し、フォールバックしません。`$reconcile-codex-subagents` は高度な
   サニタイズ済み読み取り専用診断に限定します。

上の通常 `codex` コマンド二つが pre-Setup trust boundary です。direct-native Mach-O、sanitized
PATH、明示的な Git/Python binding、署名検証、cutover/rollback、隔離 maintainer acceptance は
強化された高度な資料であり、通常ユーザーの手順ではありません。この三段階だけで十分です。

| 依存クラス | 項目 | Setup の動作 |
| --- | --- | --- |
| `bundled-with-guardian-plugin` | Guardian、Reconciler、Setup の三つの Skill | installed Guardian Plugin に同梱 |
| `automatic-after-explicit-setup-apply` | All in Luna Plugin、固定 CLI wheel、14 ロール | Plugin は installed-but-unowned のまま。明示的に許可した `--apply` の後、receipt-backed ownership の対象は wheel、ロール、管理 venv だけ |
| `external-prerequisite` | macOS、Codex CLI、`portable-full` 用 Python `>=3.11`、`/usr/bin/git` を提供する動作中の macOS Command Line Tools（または別の信頼できる provider）、GitHub HTTPS | 管理トランザクション外で提供・信頼。Setup は provider を検出するだけで、インストールしない |
| `detect-then-separately-authorize` | lifecycle hook/Node を含む完全な Ponytail Plugin、Headroom、Node 統合、HostAdapter relay | 検出だけ。静かにインストール、有効化、信頼しない |
| `account-dependent` | Codex ログイン、model entitlement/クォータ、private OMC | 新しいアカウント/能力証拠が必要 |
| `none` | MCP（`mcp_servers: []`） | MCP 設定、サーバー、認証情報をコピーしない |

## 権限境界とデリバリーワークフロー

デリバリーでは `$codex-workflow-guardian` を明示的に呼び出します。ユーザーの目標と計画を、
一致する実行の発見または再開、正確な TaskGraph フィンガープリントと共有 host-slot
確認、制限された作者ルーティング、compact/resume 復旧、ソース・テスト・ライブ受け入れ
の分離へ順に進めます。アクションとライブ状態の権威はネイティブ Codex です。

All in Luna、OMC、Headroom、Ponytail は任意で、能力を検出して使うコンポーネントです。
All in Luna は任意の永続 TaskGraph ランタイムで、Guardian は構成・権限・受け入れの入口
ワークフローです。利用できる場合、All in Luna は一致する永続 TaskGraph/Store/依存関係/復旧/ルート完了を、
OMC は Sol→Spark/Luna の作者ルーティングと独立検証を、Headroom は転送観測だけを、
Ponytail `lite` は実装作者だけが使用します。14 個のバンドル OMC 互換ロールテンプレートは
サニタイズ済みのルーティングテンプレートであり、完全な/private OMC、Skill、モデル entitlement、
クォータのインストールではありません。能力がない場合は owner 状態の規則に従い、コンポーネントを偽装しません。ネイティブ/手動フォールバックは、durable run がなく、
fresh canonical lookup で active matching run がないと証明され、`HostAction` も発行されていない場合だけ
許可します。durable run、active matching run、または発行済み `HostAction` が scope を所有したら、その owner
を保持して停止します。`ACTION_RELAY_REQUIRED` と `HOST_CAPABILITY_BLOCKED` は owner/blocker の結果であり、
フォールバックは禁止です。`resume` は保存済み `goal_ref`、`revision`、`intent_id`、`run_ref` を使い、prompt
文面で再照合したり `run_ref` を再生成したりしません。Store/schema/digest の不一致は
`PROTOCOL_INTEGRITY_FAILURE` です。本リポジトリには MCP サーバー、リモートツール、サービスは
ありません。リモートのツール/サービス境界が存在しないためです。
Setup がインストールした Plugin は receipt-owned リソースではなく状態の観測対象です。Codex CLI
`0.146.0` はどの操作が Plugin を作成したかを証明しません。したがって Plugin の削除は、保持確認後に
別途明示確認したネイティブ Codex 操作としてのみ行います。legacy `owned_plugins` フィールドは無視します。
All in Luna は削除の別途確認まで `installed-but-unowned` として保持します。まず対象 Guardian selector の正確な
identity と保持 baseline を確認し、正確な Guardian selector だけを削除します。この selector 手順では marketplace
に依存がないことを先に証明する必要はありません。Guardian selector の削除後の fresh な Plugin list を再取得し、
依存がない場合だけ marketplace を削除します。依存が残る場合は登録を保持して停止します。

Guardian には `author-lite` の最小ルールが内蔵されており、Ponytail なしでも core の等価な流れを
保ちます。要件/実装/検証の役割を分離し、typed handoff と独立した証拠を使います。Ponytail は強化項目です。

既定の durable Store は
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db` です。すべての All in
Luna CLI 呼び出しで、この canonical absolute path を `--db` として明示します。まず、目標の結果の
意味を保つが秘密を含まない `goal_identity` を凍結します。credential、secret、raw token、PII/顧客識別子は、
呼び出し元が提供する非秘密の `<credential-ref>`、`<artifact-ref>`、`<pii-ref>` placeholder に置き換えます。
保護された値から placeholder を導出せず、元の文字列を ID やログへ書きません。決定的な `intent_id` は
`cwfg-` と、`protocol`、canonical workspace、凍結した `goal_identity` を含む canonical JSON の
SHA-256 先頭 16 個の小文字 hex からなり、21 文字で不透明です。Store には認可された完全な `RunIntent` を
保持できますが、`goal` field はサニタイズしたままにし、保護された値は別途認可された reference のみで渡します。
Store に記録がなければ一度だけ start し、一つの一致する active 記録なら status/reconcile/resume のみを使います。
terminal、mismatch、複数一致または曖昧な場合は停止し、明示的な新しい revision を要求します。信頼できる
Setup ディレクトリと managed CLI がなければ durable run を主張しません。

core-only の zero-match を許可する唯一の証明は、機械可読な
`host_capability.zero_match_evidence` 契約です。正確な identity registry は
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json` で、
ディレクトリは `0700`、原子的に保存する `guardian-run/v1` entry は `0600` とし、canonical
Store と正確な `intent_id` に一致しなければなりません。runtime-available の証明には安全な
canonical path、正確な Store query の成功、発行済み `HostAction` がないこと、Store/identity
結果の一致が必要です。最初の成功した `start` では dispatch 前に非秘密 identity を原子的に保存し、
失敗したら新しい run を保持して `recovery-required` で停止します。Store、sidecar、identity、
schema、digest の不一致は `PROTOCOL_INTEGRITY_FAILURE` です。

All in Luna が fresh に `unavailable`（`configured-unverified` や `blocked` ではない）と証明され、
canonical Store と sidecar、および正確な identity entry が symlink を追わず fresh に存在しないと
証明され、現在の owner/発行済み `HostAction` がなく、既知の legacy/alternate Store も存在しない場合
だけ `FRESH_ZERO_MATCH` を返せます。許可されるのは一回の bounded な非 durable native/manual flow
だけで、exactly-once や `resume` を主張しません。Store/identity が存在する、危険または unreadable、
状態が不一致、または既知の legacy/alternate Store を検査できない場合は `OWNER_LOOKUP_BLOCKED`
（fallback は false）で停止します。任意のディスクをスキャンしません。

Reconciler は高度なサニタイズ済み読み取り専用診断ツールです。dispatch、close、archive、delete、
repair、publish、deploy、ライフサイクル対象の変更を行いません。

外部公開、デプロイ、破壊的操作、ライフサイクル操作には正確な都度のユーザー許可が必要
です。ソース/テストが成功しても、ライブ、ブラウザー、デバイスの受け入れを意味しません。
それらには別の最新証拠が必要です。

「全自動処理」や「coordinate this delivery」のような自然言語は使用上の提案にすぎません。
Setup、公開、デプロイ、ライフサイクル操作を許可するものではありません。Skill の認可は
正確な `$codex-workflow-guardian`、`$reconcile-codex-subagents`、
`$setup-codex-workflow-guardian` の呼び出しだけです。三つすべてで
`allow_implicit_invocation: false` を維持します。

## プロファイルと証拠の境界

| プロファイル | 受け入れ境界 |
| --- | --- |
| `core` | Guardian Plugin と三つの Skill（Guardian、Reconciler、Setup）。 |
| `portable-full` | `core` に固定 All in Luna Plugin/CLI wheel と 14 ロールを加える；Python `>=3.11`。Setup は Ponytail をインストールせず、hook を有効化も信頼もしない。`--apply` は installed までで、HostAdapter の呼び出し可能性はインストール後の新しい task で受け入れます。 |
| `machine-integration` | Desktop/CLI skew、Headroom の存在/バージョン/健全性/ルーティング、ライフサイクル hook が必要とするユーザー Node、hook、本機起動。別の認可とライブ受け入れが必要。 |
| `account-dependent` | Codex ログイン、モデル entitlement/クォータ、private OMC Skill。新しい receipt が必要。GitHub ネットワーク接続は別の外部前提で、Codex アカウント能力の証明ではありません。 |

ソース/テスト、インストール済み状態、呼び出し可能な能力、動作/ライブ証拠は分離します。
緑のテスト、インストール receipt、ロールテンプレートだけではモデルアクセスや現在の
ブラウザー/ログ/DB/デバイス動作を証明しません。portable-full は機能上の公開等価性を
目標とし、このマシンの汚れた RC2/OMC/Headroom 状態をバイト単位でコピーしません。本プロジェクトの対象は
GitHub-backed Codex repo marketplace source であり、universal public Plugins Directory への掲載を意味しません。

単独の `ready` ラベルは使いません。意図したファイルと一致する receipt が存在する場合だけ
`installation-ready`、設定は存在するが新しい呼び出し可能能力の receipt がない場合は
`capability-configured-unverified`、宣言したインストール受け入れが成功した場合だけ
`acceptance-installed` と報告します。いずれも現在のライブ動作を証明しません。

## 依存関係マトリクス

| コンポーネント | 必須または検証済みの基準 | 境界 |
| --- | --- | --- |
| Codex Desktop/CLI | 最低 `0.146.0`、ローカル CLI は `0.146.0` を検証済み（Desktop 埋め込み版は異なる場合があります） | ネイティブ Codex がアクションと権限の権威です。`plugin list --json`、marketplace、`plugin add --json` の JSON 形状は CLI `0.146.0` だけで検証し、schema 不一致は fail closed です。 |
| Python | バンドルスクリプトと Setup Skill は `3.9`、管理対象 All in Luna CLI は `>=3.11` | バイトコードや可変の依存解決は不要です |
| All in Luna | `2.0.0-rc.3`、commit `723088a7c0d7342f077ad675c6ea72d7e3996536`、Apache-2.0、wheel SHA-256 `e2e59ce76deab1b39efe6feb1b64c983101c313268ce52dfa25787b77295dee1` | 任意の永続ランタイム。正確な wheel のみ、依存解決は行いません |
| Ponytail | `4.9.0`、commit `2ed6c52c9d7e5e56942508591085fd45dea277d3`、MIT | Ponytail Skill 自体は Node 不要。ライフサイクル hook はユーザー提供 Node を要求する場合があり、Setup はインストール、有効化、信頼をせず、machine integration が別途 Node を検証します |
| OMC 互換ロール | 完全/private OMC ではない 14 個のサニタイズ済みテンプレート：`code-reviewer`、`coder`、`debugger`、`document-specialist`、`executor`、`explore`、`luna-coder`、`luna-worker`、`security-reviewer`、`sol-lead`、`spark-verifier`、`test-engineer`、`tracer`、`verifier` | 新しい role/model/reasoning receipt があるまで model entitlement は `configured-unverified` です |
| Headroom | `headroom-ai` `0.34.0`、canonical リポジトリ [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom)、Apache-2.0、Python `>=3.10` | 検出/手動統合のみ。artifact と transitive dependency の hash は lock せず、存在/版は readiness、health、routing、launch、初期化、信頼を証明しません |
| Node.js | `machine-integration` でライフサイクル hook が必要とするユーザー提供 `node` ランタイム | Ponytail Skill には Node 不要。hook のためだけに使い、Setup は自動インストールも信頼もしません |
| Xcode Command Line Tools | `portable-full` が使う固定 `/usr/bin/git` を提供する動作中の provider | 外部前提。Setup は検出するだけで、インストールしない |
| HostAdapter / host relay (`host_capability`) | Codex Desktop の正確な top-level relay `codex_app__create_thread`、または CLI/IDE の native `lane-direct` | Setup はインストールしません。durable run/`HostAction` がなく fresh canonical zero-match を証明した場合だけ、制限された native/manual fallback を許可します。owner があり relay が未検証なら `ACTION_RELAY_REQUIRED`、fresh に正確な tool がないなら `HOST_CAPABILITY_BLOCKED` とし、どちらも owner を保持して停止し、fallback は禁止です |
| GitHub ネットワーク | GitHub-backed Codex repo marketplace source と固定 artifact host への HTTPS 接続 | 接続性だけを示し、Codex ログイン、model entitlement/クォータ、private OMC とは別です |
| MCP とその他のリモートサービス | 不要です；`mcp_servers` は `[]` | ホストの MCP 設定、サーバー登録、認証情報を決してコピーしません。Memos、Context7 など外部サービスは意図的に除外します |

## macOS の高度なインストールと maintainer acceptance

インストールは意図的に二段階で、不変の参照を使います。Setup の対象は
`portable-full` で、Guardian Plugin 自体が `core` を提供します。通常のインストールには
明示的に選んだ永続 Codex home を使います。一時 home を通常利用に使い、正常なタスクへ
自動的に現れると考えないでください。

### メンテナー向け高度永続インストール

既存の永続 home、Codex 実行ファイル、Git 実行ファイル、信頼できる Python 実行ファイル、監査済みの
正確な 40 桁コミットを定義し、同じ対象に Guardian Plugin をインストールします。`portable-full` は
`PYTHON_BIN >=3.11` と `venv`/`ensurepip` を要求します。信頼できる現在の明示インタープリター、明示指定した
インタープリター、または固定 macOS 候補 `/opt/homebrew/bin/python3.14` から `python3.11`、続いて
`/usr/local/bin/python3.14` から `python3.11` だけを使います。Setup の自動選択はこの固定リストだけを
探し、ambient PATH の任意の Python ランチャーを実行しません。

実行ファイルの信頼は明示的です。`GUARDIAN_CODEX_BIN`、`GUARDIAN_GIT_BIN`、
`PYTHON_BIN` を canonical absolute path に固定し、ambient `PATH` から解決しません。
native Codex は OpenAI Developer ID の署名検証後だけ起動します。Homebrew の
JS launcher は versioned bundled native Codex を見つける制約付き静的ロケータに
限り、Setup は Node や JavaScript wrapper を実行しません。信頼境界は root と
current UID です。標準 Homebrew prefix だけは `extended ACL` がない場合の
`admin group-write` を許可します。same-UID、root、trusted admin の攻撃者は防御しません。

通常の既存 `CODEX_HOME` のために chmod や移動を行う必要はありません。current UID が所有し、
group/other が書き込み不可で extended ACL がなければ `0700`、`0750`、`0755` を受け入れます。
Setup は機密性のある `workflow-guardian` ディレクトリを `0700`、receipt、journal、sidecar を
`0600` として保護します。maintainer の隔離受け入れだけは別の `0700` 一時 home を使います。

ディスク上の receipt は `private-local` です。stdout は `public-redacted` projection
だけで、path、hash、device/inode、env、raw logs を除外します。失敗時は receipt、
journal、quarantine entry を保持し、調査中に上書きしません。
stdout の契約は `codex-workflow-guardian/bootstrap-stdout/v1` です。`schema`、`projection`、
`generation`、`mode`、`platform`、`status`、`canonical_source_verified`、
`guardian_ref_verified`、`existing_receipt`、`installation_status`、`capability_status`、
`acceptance_level`、機密情報を含まない `planned` の count/components/action、
`conflict_summary` の count/categories、component の name/status/version 要約、rollback action 要約、
notes、conflicts の name/reason pairs、failure、recovery だけを保持し、絶対/相対 path、
`*_relative`、SHA/hash/digest/commit/selector、device/inode、ownership/provenance、environment values、
credentials、raw logs を除外します。
`acceptance_level` は `preflight|installed|uninstalled|transaction-recovery` のいずれかです。新しいタスクの
実際の receipt が返るまで `capability_status` は `configured-unverified` のままです。`planned` は
機密性のない All in Luna Plugin/CLI/venv と 14 ロールの count/action 要約だけを含み、path や hash は含みません。

Git-backed repository marketplace を追加し JSON を保存します。これは Universal Public Plugins Directory
からのインストールではありません。`GUARDIAN_CODEX_BIN` には、署名済みの vendor native
Mach-O Codex バイナリを直接指定します。`/opt/homebrew/bin/codex` や
`/usr/local/bin/codex` は Node/JavaScript wrapper の場合があり、下の制限 PATH では
Homebrew を意図的に除外しているため、そのまま使わないでください。信頼できるインストールから
Apple Silicon または Intel 用の vendor バイナリを解決し、次の検証を通してください。

```sh
GUARDIAN_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"  # この永続対象を確認
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
GUARDIAN_REF="AUDITED_COMMIT_SHA"  # 監査済み 40 桁 hex に置換
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

停止。`$MARKETPLACE_ADD_JSON` の JSON、名前/ソース、正確な ref、予期しない登録がないことを人手で確認します。
確認できなければ続行しません。確認後だけ Guardian Plugin を追加します。

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。`$PLUGIN_ADD_JSON` の schema、正確な `pluginId`、想定された `name` と
`marketplaceName`、空でない `version`、`authPolicy`、空でない `installedPath` を確認します。
フィールドが欠落または想定外なら停止し、パスを推測しません。確認後にだけ root をコピーして検証します。

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

停止。確認した Plugin root に bootstrap、lock、marketplace、14 role asset があることを確認します。
新しい Codex task から `$setup-codex-workflow-guardian` を明示的に呼び、source checkout から apply
path を派生しません。能力 probe と read-only `--check` は別 step で実行します。

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

停止。`$CHECK_RECEIPT_JSON` の public-redacted stdout projection で非機密の
`existing_receipt` を確認します。`absent` は初回 check で private receipt がなく、public logical
plan だけをレビューする状態です。`present` の場合だけ、前回成功した `--apply` が残した変更の
ない固定 receipt pair を検査します。provenance、schema、capability、conflict、所有予定をレビューし、
非ゼロ check を apply の許可とはみなしません。明示的な許可後に別 step で apply します。

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

`$APPLY_RECEIPT_JSON` は public-redacted stdout projection として、private-local live receipt pair と
別にレビューします。marketplace/Plugin command は選択した home のみを書き、repository checkout は変更しません。
失敗時は receipt、transaction journal、quarantine entry を保持し、上書きしません。通常利用には新しい Codex task を開始し、三つの Skill は
`allow_implicit_invocation: false` の明示呼び出しを使います。

Guardian、Reconciler、Setup はすべて `allow_implicit_invocation: false` の明示呼び出しです。

### 通常アンインストール

新しい Codex task を開始し、`$setup-codex-workflow-guardian` を明示的に呼び出して、安全なアンインストールを
依頼します。Skill が正確な installed Plugin root/ref を自動的に発見・検証し、対応する receipt が所有し変更されて
いないロールファイルと管理 All in Luna venv だけを削除します。Setup が追加したすべての Plugin（All in Luna を含む）は
`installed-but-unowned` のままであり、legacy `owned_plugins` フィールドは削除を許可しません。保持確認後の
Guardian selector と marketplace の削除は、別途明示確認したネイティブ Codex 操作です。All in Luna は別途削除確認まで
`installed-but-unowned` のまま保持します。保持確認後、対象 Guardian selector の正確な identity と保持した非 Guardian
baseline を確認し、正確な Guardian selector だけを削除します。この selector 手順では marketplace に依存がないことを先に
証明する必要はありません。Guardian selector の削除後の fresh な Plugin list を再取得し、依存がない場合だけ marketplace を
削除します。依存が残る場合は登録を保持して停止します。conflict または理解できない
保持状態では停止し、receipt、journal、quarantine の証拠を保持します。

`plugin marketplace list --json` だけでは登録の存在しか証明できません。

以下の長い shell 手順は、maintainer が隔離 recovery または監査済み cutover を行うためのものです。通常ユーザーの経路ではありません。

### Maintainer/recovery uninstall（高度）

`codex plugin add --json` が返した正確な installed Plugin root を使い、source checkout からパスを推測しません。Setup uninstall は receipt が所有し変更されていないロールファイルと管理 venv だけを対象にします。Setup は Plugin selector を常に保持し、以下の保持確認後に別途明示確認した Codex 操作でのみ Plugin を削除します。失敗時は receipt、journal、quarantine の証拠を保持します。

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
GUARDIAN_REF="AUDITED_COMMIT_SHA"
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

停止。`$UNINSTALL_RECEIPT_JSON` と private receipt/journal を確認します。conflict、部分結果、所有権の変更、理解できない保持状態があれば停止します。receipt-owned uninstall は無関係な内容の削除を許可しません。

```sh
POST_UNINSTALL_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-uninstall-plugin-list.XXXXXX")"
test -n "$POST_UNINSTALL_PLUGIN_LIST_JSON" && test -f "$POST_UNINSTALL_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_UNINSTALL_PLUGIN_LIST_JSON"; then
  echo "post-uninstall plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。有効な JSON であること、正確な Guardian selector がまだ存在すること、保持した非 Guardian 行と source identity が変わっていないことを確認してから、正確な Guardian selector だけを削除します。
この selector 手順では marketplace に依存がないことを先に証明する必要はありません。

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin remove codex-workflow-guardian@onebigmoon-codex-workflows; then
  echo "Guardian selector removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。正確な Guardian selector が消えたことを確認します。他の selector が変化した場合は停止します。

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。Guardian selector 削除後の fresh な Plugin list が有効で、インストール済み selector が
`onebigmoon-codex-workflows` を参照していないことを確認します。依存がない場合だけ marketplace を削除し、依存が残る場合は
marketplace 登録を保持して停止します。下の marketplace list は登録だけを証明します。

```sh
MARKETPLACE_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace-list.XXXXXX")"
test -n "$MARKETPLACE_LIST_JSON" && test -f "$MARKETPLACE_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace list --json >"$MARKETPLACE_LIST_JSON"; then
  echo "marketplace list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。有効な JSON が、インストール済み Plugin または保持された依存関係のどれも `onebigmoon-codex-workflows` を使わないと証明した場合だけ marketplace を削除します。それ以外は登録を保持して停止します。

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace remove onebigmoon-codex-workflows; then
  echo "marketplace removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

停止。正確な marketplace 登録だけが削除されたことを確認します。失敗時は `$UNINSTALL_RECEIPT_JSON`、private receipt、transaction journal、quarantine entry を保持します。

### 保守者の隔離受け入れ

新しい一時 home はリリース検証だけに使います。上の通常インストールとは別で、ユーザーの通常の Codex
タスクを自動変更しません。同じ Python 境界と固定 3.11–3.14 候補を使います。確実な Plugin を先にインストールし、
成功した `codex plugin add --json` の `installedPath`（Plugin root）だけから `SETUP_SKILL_DIR` を派生します。
保守 checkout は、正確な監査済み commit の clean tracked checkout、完全な file/hash 検証、canonical root、
root/current UID 所有権、world-writable または未認可 group-writable でない権限、extended ACL がない場合に限り
読み取り専用検証に使えます。bootstrap、doctor、postflight は同じ trusted root から取得します。通常の Setup と
canonical `--apply` はインストール済み marketplace Plugin の bootstrap、lock、marketplace、14 role asset に
bind し、パスを推測しません。

```sh
GUARDIAN_CODEX_HOME="$(/usr/bin/mktemp -d "/tmp/codex-workflow-guardian.XXXXXX")"
test -n "$GUARDIAN_CODEX_HOME" && test -d "$GUARDIAN_CODEX_HOME" || exit 1
GUARDIAN_CODEX_HOME="$(cd "$GUARDIAN_CODEX_HOME" && pwd -P)" # macOS の正規パス
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
GUARDIAN_REF="AUDITED_COMMIT_SHA"  # 監査済みの正確な 40 桁コミット
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

停止。`$MARKETPLACE_ADD_JSON` の schema、canonical source/name、正確な ref、予期しない登録がないことを確認します。

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。`$PLUGIN_ADD_JSON` の schema、想定された `pluginId`、`name`、`marketplaceName`、
`version`、`authPolicy`、空でない `installedPath` を確認し、フィールドが欠落または想定外なら停止します。

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

停止。bootstrap、lock、marketplace、14 role asset を確認します。新しい Codex task から
`$setup-codex-workflow-guardian` を明示呼び出し、source checkout から apply path を派生しません。probe と check を別 step で実行します。

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

停止。`$CHECK_RECEIPT_JSON` の public-redacted stdout projection と `existing_receipt` をレビューします。
`absent` なら初回 check の public logical plan だけ、`present` なら前回成功した `--apply` の固定
receipt pair だけを検査します。非ゼロ check を apply 許可とみなさず、明示的に許可した後だけ apply を実行します。

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

`$APPLY_RECEIPT_JSON` と live receipt pair を別に確認します。marketplace と Plugin command は選択した home だけを書き、
repository checkout は変更しません。三つの Skill は `allow_implicit_invocation: false` の明示呼び出しです。

`--guardian-ref` は正確な 40 桁 hex commit でなければなりません。`--check` は管理対象を
直接書き込みませんが、検証済み Codex/Python probe を実行し、外部プログラムが自身の状態を
維持する可能性があります。リリース gate では新しい隔離ターゲットの tree、mtime、hash を
前後比較してください。変更、競合、必須能力の不足があると非ゼロで終了します。receipt を
確認してから `--apply` を実行してください。CLI は `--check`、`--apply`、`--uninstall` のみ
を公開します。`--uninstall` は receipt が所有し変更されていないロールファイルと管理 venv だけを
削除し、Plugin selector は削除しません。Setup が追加した全 Plugin（All in Luna を含む）は
installed-but-unowned で、legacy `owned_plugins` は無視されます。既存または変更済みの内容は保持して
競合として報告します。Python `>=3.11` が使える
場合、receipt は明示ターゲットの `venvs/allinluna/bin/allinluna` を示します。Ponytail hook は
自動で有効化・信頼されません。必要ならレビュー後に明示的に有効化してください。

Setup 後に新しい Codex タスクを開始し、OMC 互換レーンを設定済みと扱う前に role、model、
reasoning receipt を要求します。テンプレートの存在だけではモデルアクセス、Headroom
ルーティング、hook の有効化、All in Luna の永続完了を証明しません。

不変 ref を切り替える場合、同名の marketplace 登録もその ref の一部として扱います。現在の
Codex CLI の `plugin marketplace upgrade` には `--ref` オプションがないため、まず `plugin list --json`
で全 installed list を保存し、保存基準を作ります。すべての installed Plugin 行（`pluginId`、installed 状態、
source identity）を保存対象にします。Codex CLI 0.146.0 は Plugin の作成者/変更を証明できないため、
receipt-owned として除外する selector はありません。旧 receipt の uninstall はロールファイルと venv
だけに限定し、Guardian Plugin と marketplace 登録の削除は保留確認後の別の明示 Codex 操作としてから、
`--ref "$NEW_GUARDIAN_REF"` で同じ登録を追加し直します。この切り替えは他の
Plugin の削除を許可しません。登録空白中は保持 Plugin が一時的に検出できません。再追加後の
`plugin list --json` を基準と比較し、JSON/schema 無効、基準行の欠落/非 installed、予期しない source
変更があれば停止してください。

切り替え前に、旧 live の
`workflow-guardian/bootstrap-receipt.json` と
`workflow-guardian/bootstrap-receipt.sha256` を監査証拠として取得・アーカイブし、旧 Plugin root と
派生 Setup Skill パスを保持してください。旧 Setup の `--uninstall` が成功すると live の receipt
pair は消費・削除され、uninstall 後に再利用できません。アーカイブした pair を手動で復元しないで
ください。旧 `--uninstall` は All in Luna を含むすべての Plugin を保持します。Plugin の削除は保持確認後の
別途明示確認した Codex 操作だけです。既存の正確なインストールも暗黙に所有・削除しません。新しい
`plugin add --json` が成功して schema 検査を通過した後、その `installedPath`（Plugin root）からだけ新 Setup のパスを派生し、
新しい `--check` と `--apply` が成功するまで旧パスと新パスの両方を保持してください。以下の前向きな
切り替えは手動であり、自動 rollback は行われません。

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
OLD_GUARDIAN_REF="OLD_AUDITED_COMMIT_SHA"  # 旧版の正確な 40 桁 hex
NEW_GUARDIAN_REF="NEW_AUDITED_COMMIT_SHA"  # 新版の正確な 40 桁 hex
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json > "$PLUGIN_BASELINE_JSON"
```

停止。完全な基準 JSON と旧 receipt を確認し、すべての installed Plugin 行（All in Luna を含む）を保持対象にします。
Codex CLI 0.146.0 に Plugin の作成者/変更証明がないため、receipt-owned として除外する selector はありません。

```sh
"$PYTHON_BIN" -I -B "$OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF"
```

停止。旧 uninstall receipt が receipt-owned state だけを除去したことを確認します。競合または部分失敗なら停止します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。正確な Guardian selector が残り、保存基準が変わっていないことを確認してから、正確な Guardian selector だけを削除します。
この selector 手順では marketplace に依存がないことを先に証明する必要はありません。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

停止。Guardian selector の削除結果を確認した後、削除後の fresh な Plugin list を取得します。依存がない場合だけ marketplace を
削除し、インストール済み selector が `onebigmoon-codex-workflows` を参照していれば marketplace を保持して停止します。下の
marketplace list は登録だけを証明します。

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

停止。正確な登録が存在し schema が有効な場合だけ、登録を削除します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

停止。登録削除を確認し、新しい ref の marketplace を追加して JSON を保存します。

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

停止。`$MARKETPLACE_READD_JSON` の schema、canonical source/name、新 ref を確認します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。保存基準と比較します。すべての行（All in Luna を含む）が installed と想定 source を保つ必要があります。
Setup uninstall によって Plugin が欠落してはいけません。次に新 Guardian Plugin を追加します。

```sh
NEW_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$NEW_PLUGIN_ADD_JSON" && test -f "$NEW_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$NEW_PLUGIN_ADD_JSON"; then
  echo "new Guardian plugin add failed; stop" >&2
  exit 1
fi
```

停止。`$NEW_PLUGIN_ADD_JSON` の schema、想定された `pluginId`、`name`、`marketplaceName`、
`version`、`authPolicy`、非空 `installedPath` を確認してから、新 bundle を検証します。

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

停止。bootstrap、lock、marketplace、14 role asset を確認し、別 step で `--check` を実行します。

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

停止。`$CHECK_RECEIPT_JSON` をレビューし、明示的に許可した後だけ別 step の apply を実行します。

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

`$APPLY_RECEIPT_JSON` と live receipt pair を確認するまで切り替え完了とみなしません。

### 切り替え失敗後の手動 rollback

以下の rollback snippet は前向きな切り替えが失敗した後だけ実行してください。成功した前向きな
手順の続きではありません。上記と同じ明示的な変数を使用します。新 Setup のパスと receipt が利用
できる場合は、最初の snippet で新しい receipt 所有のロールファイルと venv だけを削除してください。
Plugin selector は別途確認します。利用できなければ
停止して状態を確認します。新しい uninstall receipt に conflict、部分失敗、予期しない所有権があれば直ちに停止し、
Setup が Plugin selector を一つも削除していないことを確認できなければ続行しません。アーカイブした旧 receipt は
監査証拠にすぎず、手動で復元しないでください。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

この別 snippet で Plugin JSON を確認します。保存基準の Plugin が欠けている、または installed として
報告されていなければ停止してください。Plugin selector は Setup uninstall の対象ではありません。marketplace
登録が空になる同じ切り替え窓で一時的に検出できない場合だけ、その状態を記録して再登録後に再確認しますが、
それは削除の証拠でも削除条件の緩和でもありません。空窓以外の欠落は受け入れず停止します。

```sh
"$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF"
```

その JSON が Guardian selector の存在を示した場合だけ、次の削除を実行します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。保存基準が無傷であることを確認し、正確な Guardian selector が残る場合だけ正確な Guardian selector を削除します。
この selector 手順では marketplace に依存がないことを先に証明する必要はありません。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

停止。Guardian selector 削除結果を確認し、削除後の fresh な Plugin list を取得します。依存がない場合だけ marketplace を
削除し、インストール済み selector が `onebigmoon-codex-workflows` を参照していれば marketplace を保持して停止します。下の
marketplace list は登録だけを証明します。

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

停止。正確な登録が存在し schema が有効な場合だけ登録を削除します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

停止。登録削除を確認し、旧 immutable marketplace を追加して JSON を保存します。

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

停止。`$RESTORED_MARKETPLACE_ADD_JSON` の schema、canonical source/name、旧 ref を確認し、保存基準と比較します。

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

停止。保存対象の non-Guardian 行（All in Luna を含む）が installed と想定 source を保つことを確認し、Guardian Plugin を再追加します。

```sh
RESTORED_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$RESTORED_PLUGIN_ADD_JSON" && test -f "$RESTORED_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$RESTORED_PLUGIN_ADD_JSON"; then
  echo "old Guardian plugin re-add failed; stop" >&2
  exit 1
fi
```

停止。`$RESTORED_PLUGIN_ADD_JSON` の schema、想定された `pluginId`、`name`、`marketplaceName`、
`version`、`authPolicy`、非空 `installedPath` を確認します。
切り替え前の `OLD_PLUGIN_INSTALLED_PATH` は再利用しません。新しい root から restored path を派生し、bundle を検証します。

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

停止。`$CHECK_RECEIPT_JSON` をレビューし、非ゼロ check は apply 許可ではないことを確認します。明示的な許可後に別 step で apply します。

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

新しい restored receipt pair を確認します。アーカイブ pair の復元、他 Plugin の削除、receipt-owned ロール/venv 状態外の手動削除は行いません。

各 marketplace 再登録の後には、この二つの `plugin list --json` 確認が必須です。各確認と条件付き削除は
別々の手動手順です。前の JSON 証拠なしに削除を実行しないでください。他の Plugin を削除せず、関連する
receipt-owned ロール/venv 状態の外側にあるパスを手動で削除しないでください。canonical `--check`/`--apply` の新旧
Setup パスは成功した `plugin add --json` の正確な Plugin root からだけ派生し、上記 asset 検証を通す必要があります。
source checkout は診断/読み取り専用検証だけで、canonical apply を認可しません。新しいタスクでは
`$setup-codex-workflow-guardian` を呼び出してインストール済み bundled script を解決してください。

### 三つの Skill の単独インストール（正確な監査済みコミットのみ）

Plugin が不要な場合でも、`$skill-installer` または直接のインストーラースクリプトは Guardian、
Reconciler、Setup の三つを同じ正確な `AUDITED_COMMIT_SHA` で解決しなければなりません。
`main` は決して使わないでください。

```sh
SKILL_INSTALLER="/path/to/install-skill-from-github.py"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$SKILL_INSTALLER" \
  --repo OneBigMoon/codex-subagent-reconciler \
  --ref AUDITED_COMMIT_SHA \
  --path skills/codex-workflow-guardian skills/reconcile-codex-subagents \
         skills/setup-codex-workflow-guardian
```

このフォールバックは `skill-only` であり、`core` には到達しません。インストーラーが選択した
ターゲットに三つの Skill ディレクトリを書き込みますが、installed Guardian Plugin とその git
marketplace provenance は作成しません。この経路から `core` または `portable-full` を主張
しないでください。単独 Setup Skill は文書/診断入口に限定され、canonical installed Guardian
Plugin と一致する git provenance が存在するまで `--apply` は `configured-unverified` として
fail closed します。

## Guardian provenance

portable-full には、canonical GitHub リポジトリ
`https://github.com/OneBigMoon/codex-subagent-reconciler`、ユーザーが監査した正確な
40 桁 `GUARDIAN_REF`、GitHub-backed Codex repo marketplace source resolver が返す一つの
installed Guardian、canonical git marketplace URL/root、そして checkout の Git HEAD が ref と一致
することが必要です。lock と marketplace はリポジトリ、パス、ref、artifact hash で完全一致しなければ
なりません。同梱の local marketplace は開発/テスト専用で、universal public Plugins Directory への
公開を意味しません。resolver が canonical provenance を証明できない場合、portable-full `--apply`
は `configured-unverified` として fail closed します。receipt は操作と所有権を記録するだけで、
公開者の身元、認可、署名の証明ではありません。

## 読み取り専用の整合性確認

`$reconcile-codex-subagents` には合成またはサニタイズ済み JSON だけを渡します。doctor は
明示された通常ファイルを一つだけ読み、postflight は明示された before ファイル一つと
after ファイル一つだけを読みます。v1 Schema は
診断互換として残り、厳格な v2 運用チェックは呼び出し元が生成した `scope` と安定した
`record_key` を使います。これらは相関キーであり、実行可能なライフサイクル対象ではありません。
`v1` は診断互換を維持し、未知イベントを `unsupported-event` として報告して `2` で終了します。
厳格な `v2` は schema validation 中に未知イベント種別を拒否します。

```sh
RECONCILER_SKILL_DIR="/path/to/reconcile-codex-subagents"  # この Skill の SKILL.md を含むディレクトリ
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

`RECONCILER_SKILL_DIR` は、インストール済み Guardian Plugin に対する `codex plugin add --json` の
`installedPath` からだけ設定します。使用前に plugin manifest、immutable ref、canonical marketplace provenance、
完全な lock/role asset を検証し、未検証 checkout を通常の実行 root にしません。保守の読み取り専用検証は、
正確な監査済み commit の clean tracked checkout、file/hash・所有権・権限・ACL 検証を必須とし、doctor/postflight
も同じ root に bind します。コマンドはリポジトリのカレントディレクトリに依存しません。

終了コードは `0`（一致/成功）、`1`（不一致/失敗）、`2`（無効、曖昧、判定不能、非対応）
です。不一致だが結論可能な before は修復基線にできますが、after は一致し、選択した記録
は実際にシーケンスを前進させて遷移しなければなりません。解決できた各記録は UI と一対一に
対応し（`running` と宣言された `live_state: running` は UI `active`、`terminal` と宣言された
`live_state: terminal` は UI `done`）、反対の行を集計で相殺できません。v2 は派生した
live 件数と、可視 `ui_state` 行から独立に計算した UI 行件数の両方を検査するため、集計値で
古い選択行を隠せません。変更のない終端、scope/key の変化、古い epoch、新しい `not_found`、
未知フィールド、重複キー、制御文字、安全でない識別子、安全でないファイルは fail closed です。

レポートは識別子を置換しますが、これは匿名化ではありません。スナップショット出力とすべての
v1/v2 JSON・テキストレポートは `trust: unauthenticated-consistency-evidence` を持ち、認可や
プロトコルの真実ではありません。公開分類は snapshot-claim ラベルを使います。同梱スクリプトは
標準ライブラリのみで、有界、非ネットワーク、非ポーリング、非書き込みです。ライフサイクル対象を
閉じる、中断する、アーカイブする、削除する、修復する、デプロイするなどの変更は一切行いません。

## 受け入れレベル

プロファイルと証拠セットは分離して扱います。

- **`core`：** Guardian Plugin と Guardian、Reconciler、Setup の三つの Skill。
- **`portable-full`：** `core` に固定 All in Luna Plugin/CLI wheel と 14 ロールを追加；Python `>=3.11`。Setup は Ponytail をインストールせず、hook を有効化も信頼もしない。いずれも別の machine-integration 認可とライブ受け入れが必要。
- **`machine-integration`：** Desktop/CLI skew、Headroom の存在/バージョン/健全性/ルーティング、ライフサイクル hook が必要とするユーザー Node、本機起動。Ponytail Skill 自体は Node 不要で、hook は自動信頼しません。別認可とライブ受け入れが必要。
- **`account-dependent`：** Codex ログイン、モデル entitlement/クォータ、private OMC Skill。新しい receipt が必要。GitHub ネットワーク接続は別の外部前提で、これらのアカウント能力を証明しません。

ソース/テスト（リポジトリと validator 結果）、インストール済み状態（resolver、lock、hash、
パス、receipt）、呼び出し可能な能力（Skill とロールテンプレート）、動作/ライブ（新しい
タスクとブラウザー/ログ/DB/デバイス証拠）は別の証拠です。再現可能な Git デリバリーも別に記録
します。成功基準は機能上の公開等価性で、汚れた RC2/OMC/Headroom 状態のコピーではありません。

Receipt と journal の SHA sidecar は破損や schema drift の検出だけを行い、認証や改ざん防止では
ありません。`CODEX_HOME` を書き換えられる同一 UID の攻撃者は記録と sidecar の両方を書き換えられます。
Setup は固定の管理対象 path/Plugin allowlist だけを受け入れ、所有権に関わる操作の前後で live state を
再観測します。

## 受け入れとセキュリティ

二つの unit suite、三つの Skill quick validator、一つの Plugin validator、三つのスクリプト
`--help`、JSON/TOML/README 定数チェック、bytecode なしのチェック、`git diff --check` を
実行します。ネットワークなしでグローバル Skill パスがない CI runner では公式 validator を
実行できない場合があり、その場合は release gate であって CI 実行済みとは主張しません。
これらはソース/テストの証拠だけで、デプロイ済みまたは現在ライブで利用可能だとは主張しません。正確な Schema は
[`skills/reconcile-codex-subagents/SKILL.md`](skills/reconcile-codex-subagents/SKILL.md)、
セキュリティ境界は [`SECURITY.md`](SECURITY.md) を参照してください。
