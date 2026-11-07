# 可配置 XHTTP H2 接收窗口的个人构建

完整的跨仓库需求、补丁归属和升级约束见 [个人定制说明](CUSTOMIZATIONS.md)。

基于 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) 最新**正式版**，增加可选窗口参数，并修复 unified-delay 已成功首轮测量的回退处理。窗口官方 PR：[3331](https://github.com/MetaCubeX/mihomo/pull/3331)。这是非官方构建；不填窗口参数时窗口行为保持上游默认。Xray 核心继续使用 26.3.27。

## NAT IPv4 与线路 IPv6 分路

增加 `xhttp-opts.download-settings.dialer-proxy`：不填继承主连接的前置代理；空字符串或 `DIRECT` 表示下载直连；其他名称选择下载专用前置代理。例如 NAT IPv4 上传、线路机 IPv6 下载：

```yaml
dialer-proxy: NAT-v4-Entry
xhttp-opts:
  download-settings:
    dialer-proxy: ''
    # 继续保留原有 IPv6 地址、端口、REALITY 和 XHTTP 字段
```

Provider 中的前置代理可引用同一 Provider 的节点；全局代理仍优先，不搜索其他 Provider 的同名节点。Provider 刷新后重新查找目标，发现内部循环引用时拒绝载入。循环检查也尊重全局同名节点 / 策略组优先规则；冷启动时使用本次配置的名称集合，避免误用尚未切换的旧运行配置。

顶层 `proxies` 会在加载时检查命名的上传和下载前置代理。XHTTP 传输可创建独立请求上下文，拨号时的循环检测无法覆盖所有静态引用，因此加载时拒绝自引用、混合上下行循环和不存在的下载前置代理。显式直连和已有的命名分路可正常加载。回归由 `config` 包的 `TestXHTTPDownloadDialerValidation` 覆盖。

公开 VLESS Encryption 入口继续使用 `random.0rtt`。首次连接获取票据，后续连接复用票据；服务端重启会丢失票据，旧票据请求可能失败一次，下一次连接重新握手后继续零 RTT。修复了提前断开或认证失败后没有清理旧票据的情况，也避免旧连接失败清掉并发取得的新票据。不会自动重放已发送的应用数据。

这类 NAT 分路同时需要 Panel v1.3.11 / Script v2.7.16 生成的新配置：公开入口不带 Vision flow，入口客户端关闭 TFO。原有线路至落地的 Vision 互联保留；此项需要更新相关服务端配置。仅使用接收窗口功能时不需要更新服务器。

## 下载与更新

- [下载最新构建](https://github.com/Chen017/mihomo/releases/latest)：Windows 64 位选 `mihomo-windows-amd64-…zip`，解压得到 `mihomo.exe`。Linux amd64 提供 gzip。
- [构建状态](https://github.com/Chen017/mihomo/actions/workflows/window-release.yml)：每六小时检查上游最新正式版（Asia/Singapore 02:17、08:17、14:17、20:17），也可点 **Run workflow**。上游更新后自动取源码、检查并应用补丁、测试、编译、发布。
- 相同上游提交和补丁已发布时跳过。依次检查每个补丁及已验证的兼容版本；上游已包含的修改不重复注入，兼容的上下文变化可通过干净的三方合并处理；冲突或测试失败时停止发布，上一版 Release 保留。
- 定时任务不直接替换电脑里的核心。定制 Clash Verge 会自动检测本 Fork 的新内核并发布包含它的新安装包，之后可在应用内更新；定制版内核更新入口也指向本 Fork。官方 Clash Verge 的内核更新仍会下载官方版，手动替换内核的用户需注意这一差别。
- 每版附 `checksums.txt`、`provenance.json`，记录上游提交、补丁 SHA256、源码提交和编译器。源代码标签对应实际打补丁后的代码，不是仅标记旧维护分支。
- GitHub 可能延迟定时任务；公开仓库长期无活动时可能停用 schedule，此时在 Actions 重新启用即可。

维护分支：`codex/window-builds`（本 Fork 默认分支）；官方 PR 分支：`codex/xhttp-h2-window`。定时构建从上游正式版重新取源码，维护分支中的 Go 代码本身不会被机器人强制覆盖；以 Release 的源代码标签和 provenance 为当次构建依据。

独立修复分支：`codex/delay-warm-fallback`。启用 unified-delay 时，原实现会在可选的第二次请求失败后把等待时间计入首轮延迟；API 还可能在已有有效首轮结果时返回超时。这里保留首轮真实测量值，只有二轮成功才改用二轮值。首轮失败仍报错，不增加探测次数或延长预算。测试以本地服务器确定性复现旧错误，再验证修复与真实失败识别。

NewNB 的另一类冷握手问题来自服务端两个通配入口重叠，修复已随 Xray v2.7.8 合并到 main；[调查与实测](https://github.com/Chen017/Xray/blob/main/docs/xhttp-delay-investigation-2026-10-17.md)。窗口选项不会修复真实断线，目标网站真实超时仍应显示为失败。

## 配置

字段：`h2-max-receive-buffer-per-stream`，单位为**字节**，范围 0–2147483647。仅影响 XHTTP 的 HTTP/2 接收流控；不改变 XMUX、TLS/REALITY、padding 或 HTTP/1.1、HTTP/3。窗口值会改变 H2 SETTINGS 和流控时序，不保证所有流量特征不变。

在原有节点 `xhttp-opts` 内加一行即可，其他内容保留：

```yaml
xhttp-opts:
  h2-max-receive-buffer-per-stream: 8388608 # 8 MiB
```

单栈 XHTTP 使用上面的字段。上下行分离时，若仅调整下行，将字段加到**已有** `download-settings` 内：

```yaml
xhttp-opts:
  # 原有主配置全部保留
  download-settings:
    # 原有下载地址、指纹、REALITY、reuse-settings 等全部保留
    h2-max-receive-buffer-per-stream: 8388608
```

不要用这个片段覆盖整份节点。主字段默认 0，沿用库默认值（当前 4 MiB）；下载字段未填时继承主字段，显式写 0 则恢复库默认。普通 TCP/Vision 节点无需添加。

本次 IPA 东京 Single 实测：默认 456.35 Mbps，8 MiB 722 Mbps，16 MiB 777 Mbps。16 MiB 的下载负载延迟为 140 ms，基线为 74 ms；每个方案仅测一次，8 MiB 未取得最终负载延迟。建议从 8 MiB 开始评估日常体验，不把 16 MiB 设为通用默认。

## 为什么有效

HTTP/2 接收窗口类似“已发出但还没补充接收额度的数据量”。额度用完时，发送端必须等接收端发回 WINDOW_UPDATE。往返延迟较大时，即使网口和 CPU 还有余量，单条 H2 流也可能受等待限制。

粗略量级为 `窗口大小 ÷ 有效反馈周期`。例如 4 MiB 和约 80 ms 对应约 419 Mbps；8 MiB 对应约 839 Mbps。这只是帮助理解的估算，反馈时机、并发流、TCP 拥塞和链路都会影响实际结果。更大窗口可能提高吞吐，也可能增加缓冲内存和满载排队延迟。

## Windows / Clash Verge Rev 更换与回退

优先使用 [Clash Verge Personal 安装包](https://github.com/Chen017/clash-verge-rev/releases/latest)：先备份配置、退出原应用，再覆盖安装到原目录。无需继续手动替换 Alpha 内核，后续走应用内更新。

以下步骤仅供继续使用官方应用、手动替换核心的用户参考。

1. 下载 Windows zip，并用 `checksums.txt` 核对下载文件的 SHA256；解压后执行 `mihomo.exe -v`，版本应包含 `-window.`。
2. 在 Clash Verge 备份原配置。在任务管理器找到图形客户端 `Clash Verge.exe`，选择“打开文件所在的位置”；它旁边的 `verge-mihomo.exe` 是客户端管理的稳定版核心。服务模式实际运行的核心可能在另一个目录，不要直接修改服务内部副本。
3. 关闭 TUN，并停止/退出 Clash Verge 的核心；确认原核心进程已退出，服务模式下仅关闭窗口可能仍有进程。备份原来的 `verge-mihomo.exe`，保留原文件。
4. 将新 `mihomo.exe` 改名为 `verge-mihomo.exe`，替换客户端管理的稳定版核心文件（通常与 Clash Verge 程序同目录）。所需管理员授权由用户自行处理。
5. 服务模式会使用经管理员批准的核心副本；若重启后 API `/version` 仍是旧版，需由 Clash Verge 的服务管理入口重新安装/更新服务，使它接纳当前核心，用户自行处理系统授权。不要直接覆盖受保护的服务副本，也不要降低目录权限。
6. 重启后核对**实际运行核心**版本，再在需要的 XHTTP 节点加上上面的 8 MiB 字段，重新加载配置。版本没变前不要用测速判断功能是否生效。
7. 回退：删除新字段或设为 0，再按相同步骤恢复备份的官方核心；服务模式也需同步恢复批准的副本。使用官方“更新内核”可重新获取官方版，但会失去本选项。

上述窗口功能只需处理本机 Mihomo，不需要更新或重装服务器 Xray。NAT 分路功能请同时按前面的服务端版本要求升级与部署。

## 开发、验证和发布

`codex/window-builds` 维护 `patches/`、`scripts/window_release.py` 和 `.github/workflows/window-release.yml`。修改定制 Go 行为时更新对应补丁及其回归测试。`patches/compat/` 保存经过验证的上下文变体。维护分支的 Go 文件供对照，真正编译的是从上游正式版创建并应用补丁的 `.local/source`。

安装 Git、已登录的 GitHub CLI、Python 3.11+ 和 Go 1.26.x。在全新 clone 根目录运行以下命令。Go 回归需允许本机回环连接。

```text
python -m unittest discover -s scripts -p "test_*.py"
python scripts/window_release.py prepare --verify
python scripts/window_release.py build
```

`prepare --verify --tag vX.Y.Z` 可固定某个官方正式版。`--verify` 即使已有同版本 Release 也会应用补丁，且生成的计划禁止 `publish`。Python 测试使用临时目录和模拟 GitHub 调用，无需联网。Go 测试覆盖实际 H2 SETTINGS、下载拨号继承与覆盖、Provider 刷新及循环、全局同名节点和策略组在冷启动时的优先级、零 RTT 复用与重启恢复，以及测速回退。

工作流在每六小时检查、手动触发或维护分支的补丁、脚本、发行说明和工作流变动时运行。先运行 Python 测试，再通过 GitHub API 检查稳定标签对应的源码提交和补丁指纹。已发布的组合跳过源码拉取、Go 安装与编译。新组合应用补丁，使用 `go.sum` 缓存依赖，按 Go 1.26.x、`with_gvisor`、CGO=0 和 amd64 v1 测试及构建。发布使用仓库的 `GITHUB_TOKEN`（`contents: write`）。

产物位于 `dist/`，包含 Windows ZIP、Linux gzip、`version.txt`、`provenance.json`、说明及 SHA256。发布前核对全部文件和构建计划，推送指向实际构建源码的标签，再上传草稿，上传成功后转为 Latest。若上传中断，重跑相同输入可用同一标签和草稿继续。草稿的未知附件或不同提交的同名标签会阻止发布，应先核对标签、构建计划与 Actions 日志。已发布版本保持原样。

若补丁无法应用，查看失败的补丁、`.local/source` 和 `.local/plan.json`。若测试失败，先按日志定位 Go 包，检查环境是否支持回环和依赖是否完整。现有源码目录和非空 `dist/` 会阻止重新准备或构建，失败现场可保留在原 clone，再用新 clone 验证。真机 IPv4/IPv6 链路和 Clash Verge 应用内更新仍需对应环境验收。
