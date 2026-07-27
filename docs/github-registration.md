# GitHub 注册与人工介入

Python 后端提供可暂停的 GitHub 注册流程，并由 React 工作台显示权威流程状态。
真实注册只在用户主动创建流程后通过可见 Chromium 窗口运行；自动化测试使用 fake browser，
不会访问 GitHub 或创建账号。

## 模块边界

- `backend/browser/cloakbrowser_client.py` 由服务级管理器共享一个 CloakBrowser Browser，并为每个流程创建隔离的
  Context 和 Page；关闭流程只释放其 Context，服务关闭时才释放共享 Browser。
- `backend/browser/github_register.py` 独占 GitHub 主机校验、注册选择器和页面状态识别；
  `backend/browser/temp_mail.py` 在独立上下文中读取邮箱和 GitHub 邮件。
- `backend/engine/flow.py` 管理从邮箱创建到密钥采集的状态转换、人工暂停、恢复和取消。
- `backend/engine/service.py` 跟踪全部后台任务，并在服务关闭时回收浏览器和邮箱资源。
- `backend/api/websocket.py` 为流程和人工介入频道推送版本化事件及初始权威快照。
- `src/pages/CreateFlow.tsx` 读取后端权威快照；`ManualIntervention` 只提交用户确认。

密码和邮箱验证码不会出现在 `FlowSession`、HTTP 响应或前端状态中。邮件验证码由 Temp-Mail 浏览器读取并自动
提交；CAPTCHA、手机号验证、
页面等待超时和未知页面均进入 `manual_verify`，用户必须在可见浏览器中亲自处理。人工介入默认
等待 300 秒；超时后流程进入 `error` 并释放浏览器与临时邮箱资源。

用户主动暂停采用安全点语义：邮箱创建与 GitHub 表单提交不会被重放；这些原子操作完成后再
进入 `manual_verify`。邮箱验证码轮询可以立即中断，并从同一邮箱和同一浏览器页面恢复。

## 状态边界

```text
idle -> creating_email -> github_register -> github_email_verify -> opencode_login
                                  \-> manual_verify -------/
                                  \-> error
活动状态均可进入 cancelled
```

`opencode_login` 是 GitHub 注册到 OpenCode 接入的交接状态，当前实现会继续执行 OpenCode OAuth。后续状态与
真实页面契约见 [opencode-go.md](opencode-go.md)。

## 本地 API

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `POST` | `/api/accounts` | 无请求体，固定使用 Temp-Mail，返回 `202` 并异步启动流程 |
| `GET` | `/api/flow/{flow_id}` | 返回当前权威 `FlowSession` 快照 |
| `GET` | `/api/flow/{flow_id}/screenshot/{screenshot_id}` | 启用截图后读取当前流程拥有的已遮罩 PNG；响应禁止缓存 |
| `POST` | `/api/flow/{flow_id}/resume` | 恢复等待人工处理的流程 |
| `POST` | `/api/flow/{flow_id}/manual-input` | 接受人工确认；仅在 API Key 自动复制失败时可携带格式受限的 `api_key` |
| `POST` | `/api/flow/{flow_id}/pause` | 请求流程在不会重复外部副作用的安全点暂停 |
| `POST` | `/api/flow/{flow_id}/cancel` | 取消流程并释放浏览器和临时邮箱 |

WebSocket 频道：

- `/ws/flow/{flow_id}`：连接后先发送 `flow_snapshot`，随后发送所有状态事件。
- `/ws/manual/{flow_id}`：发送初始快照、人工介入请求和失败/取消事件。

每条事件包含 `event`、`version`、UTC `timestamp`、`flow_id` 和类型化 `payload`。截图事件只携带
不可推导路径的 `screenshot_id`，不携带 PNG、base64、文件路径或页面原文。
服务端为每个连接使用容量为 8 的队列；慢客户端不会导致内存无限增长，断线也不会改变流程状态。

## 截图边界

截图由 `OPENCODE_REGISTER_SCREENSHOTS_ENABLED=true` 显式启用，默认关闭。启用后仅在 GitHub 人工介入
暂停点捕获 PNG；付款页面不捕获。浏览器在截图前遮罩 input、textarea、contenteditable、pre、code、
iframe、canvas、挑战控件、密钥单元格，以及当前流程已知的邮箱、用户名、workspace 和密码文本。

文件保存在应用私有数据目录的 `screenshots/{flow_id}/`，目录权限为 `0700`、文件权限为 `0600`，单张
上限 5 MB。默认最长保留 24 小时、每流程最多 3 张，可分别通过
`OPENCODE_REGISTER_SCREENSHOT_RETENTION_HOURS`（1–168）和
`OPENCODE_REGISTER_SCREENSHOT_MAX_PER_FLOW`（1–10）配置。读取接口只接受 UUID 标识并拒绝路径逃逸、
符号链接和非 PNG；响应使用 `Cache-Control: no-store`。流程恢复、取消、失败或完成时立即删除该流程截图，
启动和写入时也会清理过期或超量文件。

人工确认接口不接收验证码、密码或页面原文。不存在的流程返回
`flow_not_found`，非法状态返回 `flow_state_conflict`，响应遵循统一错误信封。

## CloakBrowser 边界

CloakBrowser 使用自带的 Chromium 二进制和源码级指纹实现，后端通过其异步兼容 API 启动
`headless=False` 浏览器。项目不配置代理、`humanize`、额外指纹参数或页面脚本；浏览器能力
不得用于跳过 CAPTCHA、风控、手机号验证、支付或其他人工边界。后端生命周期会在应用启动时检查
浏览器二进制，缺失时在工作线程自动下载约 200 MB；`GET /api/health` 通过 `browser_status` 返回
`initializing`、`ready` 或 `error`，`POST /api/browser/initialize` 用于显式重试。所有生产浏览器客户端
在启动 Chromium 前等待同一个初始化任务，也可以按 `docs/rules/03-commands.md` 预先安装。打包分发前必须单独确认
CloakBrowser 二进制许可证，当前版本不得把自动下载的二进制直接纳入安装包。
