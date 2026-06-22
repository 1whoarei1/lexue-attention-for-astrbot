# lexue-attention

`lexue-attention` 是一个 AstrBot 插件，也可以作为独立 Python 工具使用。它用于获取 BIT 乐学作业 DDL，支持手动查询、状态同步、每日主动推送和 DDL 前提醒。

当前登录流程参考了 `BIT101-Android` 中可复用的部分：先登录 BIT 统一认证，保留同一会话 Cookie，再访问乐学并导出 iCalendar 订阅。

## 功能

- 通过 BIT 统一认证登录乐学。
- 读取乐学 iCalendar 订阅地址。
- 获取并解析 `.ics` 日历事件。
- 标准化 DDL 字段：`uid`、`title`、`description`、`course`、`due_at`。
- 按 UID 保存本地状态，用于识别新增、变更和已提醒记录。
- 提供 AstrBot 指令：账号设置、日历订阅设置、手动查询、手动同步、定时推送。
- 所有 `/lexue` 命令仅 AstrBot 管理员可用，非管理员发送时不会触发乐学回复。
- 提供命令行工具：获取 DDL、同步状态、登录诊断。

## AstrBot 安装

从 GitHub 下载或克隆本项目后，将整个项目目录打包为 zip，再在 AstrBot 插件页上传安装。

从旧版本升级时，建议先在 AstrBot 插件页卸载旧插件，或删除旧目录后再上传新版，避免旧文件残留：

```text
data/plugins/astrbot_plugin_lexue_attention
```

插件运行状态保存在 `data/plugin_data/astrbot_plugin_lexue_attention/state.json`，删除插件代码目录不会删除 DDL 状态。

压缩包内必须只有一个顶层目录，顶层目录名建议保持为：

```text
astrbot_plugin_lexue_attention
```

正确的 zip 内部结构应类似：

```text
astrbot_plugin_lexue_attention/
  main.py
  metadata.yaml
  _conf_schema.json
  requirements.txt
  src/lexue_attention/...
```

如果手动复制插件目录，目录名必须是：

```text
astrbot_plugin_lexue_attention
```

复制到 AstrBot 的插件目录后，重载或重启 AstrBot：

```powershell
Copy-Item -Recurse .\astrbot_plugin_lexue_attention <AstrBot>\data\plugins\astrbot_plugin_lexue_attention
```

## 快速配置

在你希望机器人推送 DDL 的群聊或私聊中依次发送：

```text
/lexue bind
/lexue account <学号> <统一认证密码>
/lexue daily 08:30
/lexue interval 60
/lexue sync
```
或者使用日历订阅地址（未得到验证的方式）

```text
/lexue bind
/lexue calendar <乐学 iCalendar/ICS 订阅地址>
/lexue daily 08:30
/lexue interval 60
/lexue fetch
```


## 主动推送怎么设置

插件的“主动回复”实际是定时主动推送，不需要用户每天触发命令。必须满足三个条件：

1. 在目标群聊或私聊中发送 `/lexue bind`。
2. 设置数据来源：`/lexue calendar <ics地址>` 或 `/lexue account <学号> <密码>`。
3. 开启每日推送或间隔同步。

每日固定时间推送当前 DDL：

```text
/lexue daily 08:30
```

这会开启 `enable_daily_push`，并在每天 `08:30` 向已绑定会话发送 DDL 列表。

间隔同步新增、变更和临近提醒：

```text
/lexue interval 60
```

这会开启 `enable_interval_sync`，每 60 分钟检查一次。如果发现新增 DDL、DDL 时间变化，或到达提醒节点，就会主动推送。

默认提醒节点是：

```text
[72, 24, 6]
```

含义是 DDL 前 72 小时、24 小时、6 小时各提醒一次。可以在 AstrBot 插件配置页修改 `reminder_milestones_hours`。

查看当前是否绑定和开启：

```text
/lexue status
```

状态里需要看到：

```text
主动推送会话：已绑定
每日推送：开启
自动同步：开启
```

## 管理员权限

插件命令默认使用 AstrBot 的管理员权限过滤器。只有 AstrBot 管理员发送的 `/lexue ...` 命令会触发插件处理；其它用户发送相同命令时，不会返回乐学相关信息。

管理员身份在 AstrBot 本身配置，不在本插件里单独维护。设置管理员后，再由管理员执行：

```text
/lexue bind
/lexue status
```

主动推送仍会发送到 `/lexue bind` 绑定的会话；普通用户不能通过命令修改账号、日历、推送时间或触发 DDL 查询。

## AstrBot 指令

- `/lexue help`：查看插件帮助。
- `/lexue bind`：绑定当前群聊或私聊，用于主动推送。
- `/lexue account <账号> <密码>`：保存 BIT 统一认证账号和密码。
- `/lexue calendar <ics地址>`：保存乐学 iCalendar 订阅地址。
- `/lexue daily <HH:MM>`：设置每日 DDL 推送时间，并开启每日推送。
- `/lexue interval <分钟>`：设置自动同步间隔，并开启间隔同步，最小 5 分钟。
- `/lexue fetch`：手动获取当前 DDL 列表，不更新提醒状态。
- `/lexue sync`：手动同步 DDL，更新本地状态，并输出新增、变更和提醒。
- `/lexue status`：查看当前配置状态。

也可以使用中文别名：

- `/乐学 帮助`
- `/乐学 绑定`
- `/乐学 日历 <ics地址>`
- `/乐学 每日 08:30`
- `/乐学 间隔 60`
- `/乐学 查看`
- `/乐学 同步`
- `/乐学 状态`

## AstrBot 配置项

这些配置可以在 AstrBot 插件配置页修改：

- `username`：BIT 统一认证账号。
- `password`：BIT 统一认证密码。
- `calendar_url`：乐学 iCalendar 订阅地址。推荐优先使用。
- `lexue_base_url`：乐学站点地址，默认 `https://lexue.bit.edu.cn`。
- `auth_method`：登录方式，默认 `android`。
- `push_session`：主动推送会话。通常由 `/lexue bind` 自动写入。
- `daily_push_time`：每日 DDL 推送时间，格式为 `HH:MM`。
- `enable_daily_push`：是否开启每日推送。
- `check_interval_minutes`：自动同步间隔分钟数。
- `enable_interval_sync`：是否开启间隔同步。
- `reminder_milestones_hours`：提前提醒小时数，例如 `[72, 24, 6]`。
- `max_events`：单次最多展示的 DDL 数量。
- `timezone`：时区，默认 `Asia/Shanghai`。

## 使用建议

优先使用 `calendar_url`，原因是：

- 不需要保存统一认证密码。
- 安装和推送更稳定。
- 登录流程变化时受影响更小。

只有在没有日历订阅地址时，再使用：

```text
/lexue account <学号> <统一认证密码>
```

如果曾经把密码粘贴到群聊、公开日志或不可信终端里，建议立即修改统一认证密码。

## 常见问题

### 旧版本升级后出现导入错误或配置字段错误

这通常是升级时 AstrBot 插件目录里混用了旧文件和新文件。处理方式：

1. 在 AstrBot 插件页卸载旧插件。
2. 确认旧插件代码目录已删除：`data/plugins/astrbot_plugin_lexue_attention`。
3. 重新将整个项目目录打包为 zip，并上传到 AstrBot。
4. 重启或重载 AstrBot。

新版已删除黑名单和白名单机制，改用 AstrBot 管理员权限过滤。升级时仍建议清掉旧代码目录。

### Failed to resolve 'sso.bit.edu.cn' 或 'lexue.bit.edu.cn'

这是 AstrBot 运行环境的 DNS 或网络问题，不是 DDL 解析逻辑问题。常见原因：

- AstrBot 部署在 Docker、服务器或面板环境里，容器 DNS 不可用。
- 当前网络无法访问 BIT 统一认证或乐学。
- 服务器不在校园网或没有配置代理/VPN。
- 临时 DNS 故障。

可以进入部署 AstrBot 的机器或容器里测试：

```bash
nslookup sso.bit.edu.cn
nslookup lexue.bit.edu.cn
curl -I https://lexue.bit.edu.cn/
```

如果使用 `calendar_url`，插件通常只需要访问乐学日历地址；如果使用账号密码，插件还需要访问 `sso.bit.edu.cn`。

## Python 命令行使用

安装开发依赖：

```powershell
python -m pip install -e ".[dev]"
```

在源码目录直接运行时，可以设置：

```powershell
$env:PYTHONPATH='src'
```

使用账号密码获取 DDL：

```powershell
python -m lexue_attention fetch --username "your_student_id" --ask-password --auth-method android --json
```

使用环境变量测试：

```powershell
$env:BIT_SSO_USERNAME="your_student_id"
$env:BIT_SSO_PASSWORD="your_password"
python -m lexue_attention fetch --auth-method android --json
```

同步本地状态：

```powershell
python -m lexue_attention sync --username "your_student_id" --ask-password --auth-method android --json
```

登录诊断：

```powershell
python -m lexue_attention diagnose-login --username "your_student_id" --ask-password
```

诊断输出会隐藏 ticket，不会打印密码或 Cookie。

## 项目结构

```text
main.py              # AstrBot 插件入口
metadata.yaml        # AstrBot 插件元数据
_conf_schema.json    # AstrBot WebUI 配置结构
requirements.txt     # AstrBot 安装依赖
src/lexue_attention/
  auth.py            # BIT 统一认证登录
  lexue.py           # 乐学 sesskey、日历导出和 ICS 获取
  ics.py             # iCalendar VEVENT 解析
  models.py          # DDL 数据模型
  state.py           # JSON 状态存储
  reminder.py        # 提醒计划
  core.py            # 插件和 CLI 共用入口
  astrbot_adapter.py # AstrBot 配置、格式化和适配逻辑
  cli.py             # 命令行入口
  diagnostics.py     # 登录诊断
docs/
  upstream-mapping.md
tests/
```

## 运行状态文件

AstrBot 插件运行时会把 DDL 状态保存到：

```text
data/plugin_data/astrbot_plugin_lexue_attention/state.json
```

状态文件不保存在插件代码目录中，因此更新插件后，已记录的 DDL 和提醒状态仍会保留。
