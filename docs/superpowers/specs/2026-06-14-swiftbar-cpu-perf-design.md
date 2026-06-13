# SwiftBar CPU 性能优化设计

## 背景

当前插件以 `plugin.3s.sh` 命名,每 3 秒执行一次。改进 hook/statusLine 后,JSONL 解析其实已经变成兜底——大部分状态变化是 hook 即时触发的。但 SwiftBar 仍按固定间隔执行,即便没有事件也照跑,造成持续 CPU 开销(在多项目活跃会话场景下尤为明显)。

实测过的两个事实约束了设计:

1. **`open -g URL` 同步耗时 60-190ms**——不能在 hook 里直接同步调用,会拖慢 Claude Code。
2. **SwiftBar 不会去重 URL 刷新**——20 次 URL 连发 = 20 次插件运行。客户端必须自己防抖。

## 目标

- 降低空闲会话下的 SwiftBar CPU 占用(去掉密集轮询)
- 状态变化时菜单栏在 ~1s 内反映出来(而不是 3s)
- 兼容未配置 hook 的项目(回落到固定间隔轮询)
- 不破坏现有用户的菜单栏图标位置

## 非目标

- 不追求亚秒级响应(用户可接受 ~1s 延迟)
- 不重写状态解析逻辑(复用现有的 `.cc-meta.json` / `.cc-status.json` / JSONL 三层 fallback)

## 架构

三件事联动:

1. **轮询间隔从 3s 拉长到 10s**——文件名 `plugin.3s.sh` → `claude-code.10s.sh`。命名同时改变是因为 SwiftBar 用文件名 `<name>.<refresh>.<ext>` 当 plugin name,改名能让 URL refresh 的 `name=` 字段唯一(`claude-code`),避免和别的插件的 `plugin` 撞名。
2. **hook 主动推送**——`cc-status-writer` 在写完 `.cc-status.json` 后异步触发 `swiftbar://refreshplugin?name=claude-code`,让 SwiftBar 立刻刷新插件,而不是等 10s 轮询。
3. **trailing-edge 防抖**——`cc-status-writer` 用 1s 延迟队列合并连发的 hook 事件,避免一次工具调用引发多次刷新。

10s 是兜底:即使 hook 完全失效(用户没装、Claude Code 崩溃),状态最迟 10s 内也会刷新一次。

## 组件设计

### 1. 插件文件重命名

将 `claude-code.swiftbar/plugin.3s.sh` 重命名为 `claude-code.swiftbar/claude-code.10s.sh`。

**为什么改名**:SwiftBar 通过文件名后缀决定刷新间隔(`.10s.`),并通过文件名前缀作为 plugin name(用于 URL refresh)。原名 `plugin` 太通用,易撞名;改成 `claude-code` 唯一稳定。

**副作用**:SwiftBar 把"菜单栏图标位置"按 plugin filename 存在 `com.ameba.SwiftBar` 的 `NSStatusItem Preferred Position <filename>` 偏好里。改名后老用户的图标会跳到默认位置。`install.sh` 负责迁移这一项偏好。

### 2. cc-status-writer 防抖推送

在 `cc-status-writer` 写完 `.cc-status.json` 后,追加 trailing-edge debouncer 逻辑:

```bash
# Trailing-edge debounce: collapse hook bursts into one URL refresh.
# Multiple hooks within 1s share a single trailing worker.
PENDING="/tmp/cc-swiftbar-pending"
LOCK="/tmp/cc-swiftbar-lock"

# Mark "an event happened" — refresh worker reads this on wake.
date +%s.%N > "$PENDING"

# If a worker is already sleeping, exit (it will pick up our marker).
if [ -e "$LOCK" ]; then
  exit 0
fi

# Spawn a worker: sleep 1s, then fire one URL refresh.
(
  : > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT
  sleep 1
  rm -f "$PENDING"
  /usr/bin/open -g "swiftbar://refreshplugin?name=claude-code"
) </dev/null >/dev/null 2>&1 &
```

**关键性质**:
- 第一个 hook 触发时启动 worker,后续 1s 内的 hook 只更新 `$PENDING` 时间戳,不开新 worker
- worker 醒来后只发 1 次 URL,无论这 1s 内攒了多少事件
- 单独的孤立事件 → 1s 延迟后推送一次
- worker 进程在 LOCK 上设了 trap,异常退出也能自清理
- 用 `nohup &` 后台化,不阻塞 hook(hook 必须在几十 ms 内返回)
- LOCK 文件存在性检查有竞态(两个 hook 几乎同时跑可能都判 LOCK 不存在),但即使产生 2 个 worker,1s 后也只会发 2 次 URL,完全可接受

**最坏情况**:hook 触发后 SwiftBar 在 ~1s 后刷新。结合 SwiftBar 自身的 10s 轮询,实际感知延迟绝大多数情况下 < 1s。

### 3. install.sh 偏好迁移

`install.sh` 检测如果用户从老版本(`plugin.3s.sh` 或 `plugin.1s.sh`)升级,把图标位置偏好搬到新文件名:

```bash
NEW_KEY="NSStatusItem Preferred Position claude-code.10s.sh"
if defaults read com.ameba.SwiftBar "$NEW_KEY" >/dev/null 2>&1; then
  : # already migrated
else
  for OLD_NAME in plugin.1s.sh plugin.3s.sh plugin.10s.sh claude-code.3s.sh; do
    OLD_KEY="NSStatusItem Preferred Position $OLD_NAME"
    POSITION=$(defaults read com.ameba.SwiftBar "$OLD_KEY" 2>/dev/null || true)
    if [ -n "$POSITION" ]; then
      defaults write com.ameba.SwiftBar "$NEW_KEY" -int "$POSITION"
      echo "  Migrated menu bar icon position from $OLD_NAME → claude-code.10s.sh ($POSITION)"
      break
    fi
  done
fi
```

**幂等性**:已迁移过的安装(`NEW_KEY` 已存在)直接跳过。多次跑 `install.sh` 不会重复迁移。

**回落顺序**:从最早的 `plugin.1s.sh` 一直找到 `claude-code.3s.sh`(开发期短暂用过的中间名),取第一个能找到的位置。

## 数据流

正常 hook 链路(用户提交一个 prompt 触发工具调用):

```
UserPromptSubmit hook → cc-status-writer 写 .cc-status.json + 启动 trailing worker
PreToolUse hook → cc-status-writer 写 .cc-status.json + 仅更新 PENDING(worker 已存在)
PostToolBatch hook → cc-status-writer 写 .cc-status.json + 仅更新 PENDING
Stop hook → cc-status-writer 写 .cc-status.json + 仅更新 PENDING
[1s 后] worker 醒来 → 删 PENDING,删 LOCK,发 URL
SwiftBar 收到 URL → 跑一次 claude-code.10s.sh → 读所有项目最新状态 → 更新菜单栏
```

兜底链路(用户没装 hook):

```
[每 10s] SwiftBar 自动跑 claude-code.10s.sh → JSONL 解析 → 更新菜单栏
```

## 错误处理

- worker 中 `sleep 1` 被杀:LOCK 上的 `trap` 清理,下次 hook 能正常起新 worker
- worker 中 `/usr/bin/open` 失败:静默,下一次轮询(最多 10s)兜底
- LOCK 残留(系统异常关机):`/tmp/` 在重启时清空,自然恢复
- `swiftbar://refreshplugin` URL 在 SwiftBar 未运行时:macOS LaunchServices 静默丢弃,无副作用

## 测试

无单元测试(都是 shell 胶水)。验证手段:

1. 装好 hooks 后,触发一次 Claude Code 工具调用,观察菜单栏在 ~1s 内更新
2. 在密集工具循环里(比如让 Claude 跑多个并行 Bash),用 `sudo fs_usage -f filesys SwiftBar` 或 `top -pid <swiftbar-pid>` 看 SwiftBar 是否每 1s 才被唤醒一次,而不是每个 hook
3. 临时关掉 hooks(`mv ~/.claude/settings.json ~/.claude/settings.json.bak`),验证 10s 兜底轮询仍能更新状态
4. 跑 `install.sh` 升级,观察菜单栏图标是否保持原位
5. 跑两次 `install.sh`,确认幂等

## 文档

`README.md` 需要更新:
- 仓库结构里把 `plugin.3s.sh` 改成 `claude-code.10s.sh`
- Hook 章节增加一句"hook 会触发主动刷新,菜单栏 ~1s 内反映状态变化",说明为什么 10s 兜底足够
