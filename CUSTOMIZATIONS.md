# Chen017 客户端 Fork 定制需求与维护约定

本文记录个人 Fork 的功能目标、补丁归属和升级约束，供后续维护者判断哪些差异必须保留。适用于 Mihomo、FLClash Personal 和 Clash Verge Personal；服务端由 Xray Panel / Xray Script 配合生成配置。这里的能力不代表官方客户端已经支持。

## 仓库分工与发布链

| 仓库与维护分支 | 负责内容 | 发布目标 |
| --- | --- | --- |
| [Chen017/mihomo](https://github.com/Chen017/mihomo/tree/codex/window-builds)，`codex/window-builds` | 在官方稳定版上应用 `patches/` 中的窗口、分路、Provider 与测速补丁，发布可验证的内核 | Windows amd64 内核；另有 Linux amd64 内核供独立使用与验证 |
| [Chen017/clash-verge-rev](https://github.com/Chen017/clash-verge-rev/tree/codex/personal-builds)，`codex/personal-builds` | 应用层补丁、迁移、签名更新；下载并固定上述仓库的已发布内核 | 仅 Windows amd64 NSIS 安装包 |
| [Chen017/FlClash](https://github.com/Chen017/FlClash)，`main` | 在 FLClash 稳定版及其固定的 Android 内核上应用 `.personal/app.patch`、`.personal/core.patch`，保留原生 VPN 集成 | 仅 Android arm64-v8a APK |

Windows 的链路为：官方 Mihomo 稳定版 → 定制 Mihomo Release → 定制 Clash Verge 安装包 Release → 应用内更新。Clash Verge 构建依赖 Mihomo Release；不能因为安装包已带内核，就关闭 Mihomo 仓库的更新流程。

手机构建使用 FLClash 上游版本固定的 `core/Clash.Meta` 子模块，携带单独移植的补丁；不直接下载上述 Windows 内核，也不会独立追逐 Mihomo 的每一次提交。传输行为重叠的补丁需要在两套配方中核对，手机专用处理则按平台保留。

## 必须保留的需求

### 1. XHTTP HTTP/2 接收窗口（H2 window）

历史上提到的“XHTTP HR 窗口”在本 Fork 中对应 HTTP/2 每条流的接收窗口参数 `h2-max-receive-buffer-per-stream`。

| 设置位置 | 行为 |
| --- | --- |
| `xhttp-opts.h2-max-receive-buffer-per-stream` | 主 XHTTP 传输的接收窗口 |
| `xhttp-opts.download-settings.h2-max-receive-buffer-per-stream` | 独立下载传输的接收窗口；省略时继承主设置 |
| 显式 `0` | 使用底层 HTTP/2 库默认值，而非关闭接收窗口 |

单位为字节，允许 0–2147483647。未配置时保留上游默认，不把 8 MiB 或 16 MiB 强制设成所有节点的默认值。依赖升级时应复核库默认值。

下面只是合并到已有节点的片段，不能用它覆盖完整的地址、认证或 REALITY 配置：

```yaml
xhttp-opts:
  h2-max-receive-buffer-per-stream: 8388608 # 8 MiB
  download-settings:
    h2-max-receive-buffer-per-stream: 16777216 # 可选：下载单独设为 16 MiB
```

只影响 XHTTP 的 HTTP/2 接收流控。不能通过修改 HTTP/1.1、HTTP/3、XMUX、padding 或关闭 TLS 校验来替代；窗口增大也可能增加缓冲和负载延迟。窗口功能本身不要求升级服务端 Xray。配置语义与验证方法见 [WINDOW-CORE.md](WINDOW-CORE.md)。

### 2. NAT IPv4 上传、线路机 IPv6 下载

目标是可配置地分离 XHTTP 两个方向的拨号路径：当线路机 IPv4 路由恶化时，上传经 NAT IPv4 加密入口到线路机，下载经线路机 IPv6 直连；同时保留线路机到落地机的原有代理出口能力。NAT 路径是可撤销的配置选择，不能硬编码地址、节点名或运营商。

```yaml
dialer-proxy: NAT-v4-Entry
xhttp-opts:
  download-settings:
    dialer-proxy: ''
    # 保留已有下载 IPv6 地址、端口、REALITY 等参数
```

`download-settings.dialer-proxy` 未填时继承主前置代理，空字符串或 `DIRECT` 表示下载直连，其他名称指定下载专用前置代理。上例的两处 `dialer-proxy` 属于不同层级，不是重复配置。需要覆盖省略、直连、命名代理和两个分路方向的验证。

服务端公开 VLESS Encryption 入口不带 Vision flow，入口客户端关闭 TFO；原有线路到落地的 Vision 互联仍可保留。服务端配置约束见 [WINDOW-CORE.md](WINDOW-CORE.md)，不能把客户端升级当成服务端配置已经更新。

### 3. Proxy Provider 内的链式代理

订阅采用 Proxy Provider，不能依赖用户手改缓存节点。前置代理可引用同一个 Provider 的节点；全局代理或策略组同名时优先，不跨其他 Provider 搜索。刷新订阅后应重新解析引用，并拒绝循环引用；冷启动不能借用上一份运行配置的名称集合。

### 4. 随机 VLESS 加密与零 RTT

保留公开入口的 `random.0rtt`。零 RTT 是已有有效票据后的连接复用路径，首次冷连接仍需获取票据。服务端重启后，旧票据请求可能失败一次；后续连接应重新握手并恢复复用。认证前的提前断开或失败要淘汰对应旧票据，旧连接失败不能清掉并发获得的新票据。

禁止为掩盖失败而自动重放已经发送的应用数据，也不能静默移除加密或把零 RTT 全部改成一 RTT。回归必须包含冷启动、真实票据复用、服务端重启及恢复。

### 5. Android 首次写入兼容处理

此项目前由 **FLClash 的内核补丁**提供，不要误认为已发布的 Windows Mihomo 也包含该开关。

Android 的原始 TCP、无 TLS、VLESS `random` 加密入口默认把每条连接的第一次加密写入拆成“1 字节 + 剩余内容”。二者连续写入，没有固定 sleep，也不等待服务器 ACK 或响应；冷握手与零 RTT 的首次应用写入都在加密字节流下方处理。后续写入保持原行为。

FLClash 定制内核可用 `encryption-fragment: false` 关闭，或用 `true` 显式开启。它不是所有 Android 连接的通用分片策略；其代价是额外一次写入调用，可能多一个小 TCP 包，不能称为绝对零开销。

诊断阶段的 50 ms 等待已排除，不能重新作为默认。没有证据确认负责丢包的具体设备或运营商，文档和代码不得将猜测写成根因。修改本行为前应做普通首包与拆分首包的对照，验证完整 HTTP/HTTPS 和票据复用，不能仅以一次握手成功验收。

### 6. 延迟测量应反映真实结果

启用 `unified-delay` 后，若首轮测量成功、可选第二轮失败，保留首轮真实测量值，不能把第二轮等待算入首轮结果；第二轮成功才采用第二轮值。真实首轮失败仍应失败。不要通过无条件返回正数、无限重试或延长所有测试预算掩盖故障。

内核 API 测试通过不等于应用 UI 和所有实网条件均已覆盖；间歇性失败仍需对照当时的 URL、超时、并发和日志，不能直接宣布是显示错误。

## 应用迁移和更新约束

- Windows 沿用官方 Clash Verge 的应用标识与配置目录。迁移时备份配置、退出原程序、覆盖安装到原目录；旧 Alpha 选择迁移到定制稳定内核。两个内部核心文件名是安装器/服务兼容用途，携带同一份已验证的定制核心。
- Windows 应用更新地址和内核更新地址均指向自己的 Fork。内核单独更新也应递增 `personal.N`，形成可被应用发现的新安装包；服务副本继续通过原有授权与回滚机制更新。
- Android 使用独立应用包 `io.github.chen017.flclash` 和固定签名。首次从官方版导出本地备份后导入；后续应用内更新并由系统确认安装。保留订阅、Age 密钥、节点选择和 VPN 授权的迁移验证。
- Android 应用包名与 Flutter/原生 MethodChannel 协议前缀是不同概念。现有通道使用 `com.follow.clash`；不能仅按新包名改单侧，否则会出现 MissingPluginException、内核未连接。保持跨语言协议测试。
- 两边更新必须保留永久签名身份、文件校验及版本递增逻辑。不能通过重新生成签名密钥或绕过校验解决构建失败；任何私钥、密码、订阅 URL 或真实节点凭据均不得进入文档。

各端操作与签名配置见 [Windows 维护说明](https://github.com/Chen017/clash-verge-rev/blob/codex/personal-builds/.personal/README.md) 和 [Android 维护说明](https://github.com/Chen017/FlClash/blob/main/.personal/README.md)。

## 上游跟随和维护检查

三个发布工作流均每六小时检查；没有输入变化时跳过构建。只跟随正式稳定版，应用/内核源码固定到提交，补丁冲突、测试或校验失败时停止发布，保留上一份可用正式版。自动构建成功不等于无需维护，也不代表已经完成所有真机网络验收。

Mihomo 补丁入口为 `patches/*.patch`；Windows 为 `.personal/app.patch` 和内核锁定配方；Android 为 `.personal/app.patch` 与 `.personal/core.patch`。维护分支是配方，Release 的源码标签、manifest/provenance 才描述当次实际构建。仅修改生成的 work/source 目录不能持久交付修复。

每次上游升级，应检查：

1. 是否已有等价上游实现；核对默认值、配置继承、错误处理和测试后才移除补丁，不能只看名称相同。
2. XHTTP 窗口是否进入实际 H2 SETTINGS，省略/显式 0/下载覆盖是否保持原语义。
3. Provider 刷新、全局名称优先、循环拒绝及两个方向的下载拨号是否正常。
4. 随机加密冷启动、零 RTT 复用和服务端重启恢复是否正常；Android 分段是否仍无固定等待。
5. 内核实际运行版本、安装包架构、应用内更新签名与版本排序是否正确；失败仍能保留上一版。

CI 覆盖协议、路由、窗口、更新与打包的回归；VPN 权限、服务权限、手机省电策略及真实 IPv4/IPv6 网络仍需对应平台验证。GitHub 定时任务可能延迟，长期无仓库活动也可能被停用，应检查 Actions 状态。本说明不要求制造空提交保活。
