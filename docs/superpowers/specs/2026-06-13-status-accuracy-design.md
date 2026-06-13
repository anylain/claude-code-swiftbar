# 状态检测准确度三连改造 — 设计文档

日期：2026-06-13
项目：claude-code-swiftbar
作者：panying32

## 背景与目标

当前 `plugin.1s.sh` 的状态检测分两层：
1. Claude Code hooks 写 `.cc-status.json`（事件驱动，60s TTL）— 已实现
2. 解析 JSONL + `pgrep claude` + `lsof` + ps 父链 — 启发式 fallback

经调研同类项目（`onikan27/claude-code-monitor`、`wuyuxiangX/claude-code-monitor`、
`Aura/ClaudeCodeMonitor`、`claude-code-tamagotchi`），发现两类未采用的信号能进一步提升准确度：

- **statusLine 钩子**：Claude Code 每次状态行更新时通过 stdin 推一份带 `session_id`/`cwd`/`model`
  的权威 JSON。当前实现靠扫 JSONL 头部 200 行抽这些字段，cwd 漂移后会失效。
- **进程子树检测**（`pgrep -P`）：能确定 Claude 是否真在跑 Bash/外部工具，可替代当前
  `SILENT_RUNNING_MAX=600s` 这个魔数。

目标：
1. 引入 statusLine 作为权威元数据源，但不强依赖。
2. 保留 hook + JSONL 启发式作为 fallback 链。
3. 用进程子树存在性替代静默期魔数。

## 核心约束

- **不强依赖 statusLine**：用户没配置时整个系统行为不变。
- **字段级 fallback**：meta 缺哪个字段就在哪个字段上回退到 JSONL，不做"全有全无"。
- **YAGNI**：不引入 OTLP、CPU% 监测、新状态枚举。

## 架构

引入三层数据源，按优先级合并：

```
            写者                          读者
┌────────────────────────┐       ┌──────────────────────┐
│ statusLine 钩子         │  →    │  .cc-meta.json       │ ──┐
│ (cc-meta-writer)       │       │  权威元数据,无 TTL    │   │
└────────────────────────┘       └──────────────────────┘   │
┌────────────────────────┐       ┌──────────────────────┐   │
│ Claude Code hooks       │  →    │  .cc-status.json     │ ──┤   字段级
│ (cc-status-writer,已有) │       │  事件态状态,60s TTL   │   ├─→ fallback
└────────────────────────┘       └──────────────────────┘   │   合并
                                  ┌──────────────────────┐   │
                                  │  JSONL + procs       │ ──┘
                                  │  启发式 fallback     │
                                  └──────────────────────┘
```

任何一层缺失，下一层兜底。

## 数据流(每次 plugin 1s 循环)

对每个 `~/.claude/projects/<proj>/` 目录：

1. 读 `.cc-meta.json` → 拿到 `session_id`/`cwd`/`workspace`/`model`/`output_style`/`last_seen`
2. 读 `.cc-status.json`，若 `now - ts < 60s` → 得到 `state`/`detail`
3. 读最新 jsonl 的 mtime + 末 20 条 → 拿启发式分类的 entries
4. `pgrep -x claude` + `lsof -d cwd` → 拿进程列表与各自 cwd
5. 对每个 alive 的 claude proc，`pgrep -P $pid` → 设置 `has_active_child`
6. 字段决策：
   - **cwd**：`meta.workspace.current_dir` → `meta.cwd` → `read_first_cwd(jsonl)` → 解码 proj 名
   - **host**：`read_entrypoint(jsonl)` → 进程父链(`host_from_parent`)
   - **state/detail**：`.cc-status.json`（fresh）→ 启发式 `classify()`

## 组件清单

### 新增

#### `.bin/cc-meta-writer`(statusLine 钩子)

- 从 stdin 读 statusLine JSON
- 由 `transcript_path` 推导 `proj_dir = dirname(transcript_path)`
- 写 `<proj_dir>/.cc-meta.json`,内容为镜像的字段 + `last_seen`
- 失败静默退出 0(绝不阻塞 Claude UI 渲染)

#### `install.sh` 追加 statusLine 配置

幂等地写入 `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash \"$PLUGIN_PATH/.bin/cc-meta-writer\""
  }
}
```

如果已有 `statusLine` 配置(用户可能装了别的 statusline 工具),**不覆盖,只警告**,
让用户自己决定是否要把 cc-meta-writer 串到现有 statusline 命令前面。

### 修改

#### `plugin.1s.sh`

1. 新增 `read_meta(pdir)`:读 `.cc-meta.json`,失败返回空 dict
2. `read_first_cwd` / `read_entrypoint` 降为 fallback:仅在 meta 不提供该字段时才扫 JSONL
3. `inspect_claude_procs()` 给每个 proc 加 `has_active_child` 字段
   - 实现:`subprocess.check_output(["pgrep", "-P", pid])` 非空即为 true
4. `classify()` 签名改为 `classify(entries, mtime, alive_proc, has_active_child)`:
   - 删除 `SILENT_RUNNING_MAX = 600` 常量
   - 原规则"alive_proc + RUNNING_SECS ≤ age < SILENT_RUNNING_MAX → running"
     替换为"alive_proc + has_active_child + age ≥ RUNNING_SECS → running(working…)"
   - 无子进程 + 静默 ≥ RUNNING_SECS:按 last_kind 走原 idle / needs-input 逻辑
5. 主循环把 meta 字段并入 session 字典(优先级高于 JSONL 抽取的同名字段)

### 不动

- `cc-status-writer`:工作良好,事件覆盖完整,继续作为状态权威。
- 7 态分类(`running` / `idle` / `needs-input` / `needs-permission` / `interrupted` /
  `error` / `unknown`):够用,不扩展。

## `.cc-meta.json` 文件格式

```json
{
  "session_id": "0193a...",
  "transcript_path": "/Users/.../projects/-Users-foo-bar/0193a....jsonl",
  "cwd": "/Users/foo/bar",
  "workspace": {
    "current_dir": "/Users/foo/bar/sub",
    "project_dir": "/Users/foo/bar"
  },
  "model": {
    "id": "claude-opus-4-8",
    "display_name": "Opus 4.7"
  },
  "version": "1.x.x",
  "output_style": {"name": "default"},
  "last_seen": 1733000000
}
```

字段直接镜像 statusLine 输入 + `last_seen = int(time.time())`。**不存 state**——
状态是 `cc-status-writer` 的职责,职责单一。

## 错误处理与边界

| 场景 | 行为 |
|---|---|
| statusLine stdin JSON 解析失败 | 静默退出 0,不阻塞 Claude UI 渲染 |
| `.cc-meta.json` 写入失败(权限/磁盘) | 静默退出,下次 statusLine 触发再试 |
| `.cc-meta.json` 与 `.cc-status.json` 的 session_id 不一致 | 各自独立读,state 以 status 为准,meta 只增强元数据 |
| statusLine 触发频率低于 hook | 天然滞后(每轮回复后才触发),cwd 可能滞后 1-2 秒,可接受 |
| `pgrep -P` 异常 | `has_active_child` 视为 false,降级到无该信号的判断 |
| 用户已有其他 statusLine 配置 | install.sh 检测到则警告并跳过,不覆盖 |

## 测试计划

shell + python 单文件插件,不写自动化测试。手动验证清单:

- [ ] 仅装 plugin(无 hook、无 statusLine):行为与改造前一致
- [ ] 装 statusLine 不装 hook:cwd/model 准确,state 走启发式
- [ ] hook + statusLine 都装:cwd 实时反映 cd,state 事件驱动
- [ ] 在 Claude 会话中 cd 到子目录:菜单栏 ≤ 2s 内反映
- [ ] 启动 `sleep 900` 类长 Bash:菜单栏全程 running,验证 600s 魔数已被替代
- [ ] `kill -STOP <claude_pid>`:has_active_child=false,不误报 running
- [ ] 删掉 `.cc-meta.json`:自动回退到 JSONL 头部扫描,功能不退化
- [ ] 用户已配 statusLine 时跑 install.sh:正确警告不覆盖

## 不做的事

- 不引入 OpenTelemetry / OTLP collector(超出"轻量 SwiftBar 插件"定位)
- 不监测 CPU%(纯推测信号,价值低于子进程存在性)
- 不重写 `cc-status-writer`(工作良好)
- 不扩展状态枚举(7 态足够)
- 不为 meta 增加 host 字段(statusLine 不提供该信号,沿用 entrypoint+父链推断)
