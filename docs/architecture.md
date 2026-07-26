# 架构设计文档

> 目标：个人学习场景下，批量管理多个 OpenCode 授权账号，并在额度用尽时自动切换下一个账号。

## 1. 项目概述

OpenCode 当前通过 GitHub / Google OAuth 登录，OpenCode Go 需要用户按页面实时价格订阅。本项目通过自动化以下流程来降低个人管理多个账号的重复劳动：

```
临时邮箱创建 → GitHub 注册（人工验证） → OpenCode 登录 → 跳转支付页 → 用户手动支付 → 记录 API Key → 加入号池
```

**核心原则**：
- 支付必须用户手动完成（仅自动跳转页面）。
- 所有验证码 / 风控验证均人工介入，不做自动化绕过。
- 账号失效时同时清理本地记录和 GitHub 账号。
- 续费时不再续费旧账号，而是创建全新账号走完整流程。

## 2. 设计目标与约束

| 目标 | 说明 |
|------|------|
| 跨平台 | 支持 macOS 与 Windows 桌面端 |
| 可扩展 | 邮箱边界保留稳定接口，浏览器自动化策略可插拔 |
| 安全 | API Key、GitHub 凭据本地加密存储，支持导出加密包 |
| 可控 | 每个关键步骤均可暂停等待人工确认 |
| 合规边界 | 不自动支付、不自动解验证码、不伪造身份信息 |

## 3. 技术栈

| 层级 | 选型 | 理由 |
|------|------|------|
| 桌面端 UI | Tauri + React | 包体小、性能较好、前端生态成熟 |
| 桌面端宿主 | Tauri Rust 进程 | 负责启动/管理 Python 子进程、文件系统访问 |
| 后端自动化 | Python + CloakBrowser | Chromium 页面操作与邮箱 API 集成最便捷 |
| 本地通信 | Python 本地 HTTP/WebSocket 服务 | 前端通过 Tauri 调用本地服务 |
| 数据存储 | SQLite + 字段级加密 | 轻量、易迁移、敏感信息加密 |
| 任务调度 | APScheduler / schedule | 检测账号额度、到期提醒 |
| 打包分发 | Tauri 打包 + Python 依赖嵌入 | 尽量做到一键安装 |

## 4. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Tauri + React 桌面端                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 账号列表页  │  │ 新建账号流程 │  │ 号池/配置管理页     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                        │                                    │
│                 Tauri IPC / HTTP                            │
│                        │                                    │
└────────────────────────┼────────────────────────────────────┘
                         │
            ┌────────────┴────────────┐
            │  Python 本地服务 (FastAPI/Flask + WebSocket) │
            └────────────┬────────────┘
                         │
    ┌────────────┬─────────┴──────────┬────────────┐
    ▼            ▼                    ▼            ▼
┌────────┐ ┌─────────┐      ┌─────────────┐ ┌──────────┐
│ 流程引擎 │ │ 浏览器层 │      │  邮箱 provider │ │  数据层  │
│ Engine │ │Browser  │      │   Provider   │ │ Storage │
└────────┘ └─────────┘      └─────────────┘ └──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  SQLite + 加密模块   │
              └─────────────────────┘
```

## 5. 项目目录结构

```
OpenCode-Register/
├── README.md                  # 项目说明
├── docs/                      # 详细设计文档
│   ├── architecture.md        # 本文档
│   ├── workflow.md            # 流程时序图
│   ├── database.md            # 数据模型
│   ├── api.md                 # 前后端接口
│   └── providers.md           # 邮箱服务商适配说明
│
├── src-tauri/                 # Tauri 桌面端（Rust）
│   ├── src/
│   │   ├── main.rs            # 入口：启动 Python 服务
│   │   ├── python_sidecar.rs  # Python 子进程管理
│   │   └── commands.rs        # 前端可调用的 Rust 命令
│   ├── Cargo.toml
│   └── tauri.conf.json
│
├── src/                       # React 前端
│   ├── App.tsx
│   ├── pages/
│   │   ├── Dashboard.tsx      # 账号列表与号池
│   │   ├── CreateFlow.tsx     # 新建账号流程
│   │   └── Settings.tsx       # 配置、导出
│   ├── components/
│   │   ├── ManualIntervention.tsx  # 人工介入面板
│   │   ├── AccountCard.tsx
│   │   └── LogViewer.tsx
│   └── services/
│       └── api.ts             # 调用本地后端 API
│
├── backend/                   # Python 后端
│   ├── main.py                # 本地服务入口
│   ├── api/
│   │   ├── routes.py          # REST API
│   │   └── websocket.py       # 实时日志/人工介入
│   ├── engine/
│   │   ├── flow.py            # 账号注册主流程编排
│   │   ├── steps.py           # 每个步骤的具体实现
│   │   ├── events.py          # 流程事件/暂停/恢复
│   │   ├── quota_service.py   # 额度检查业务协调
│   │   └── cleanup.py         # GitHub 删除与本地清理状态机
│   ├── browser/
│   │   ├── cloakbrowser_client.py
│   │   ├── github_register.py
│   │   ├── opencode_login.py
│   │   ├── opencode_quota.py  # 后台浏览器额度抓取
│   │   └── github_cleanup.py  # GitHub 身份核对与删除验证
│   ├── providers/
│   │   ├── base.py            # 邮箱 provider 抽象基类
│   │   └── integrations/
│   │       └── duckmail.py    # duckmail.pro 唯一邮箱实现
│   ├── storage/
│   │   ├── db.py              # SQLite 连接
│   │   ├── models.py          # SQLAlchemy / 数据模型
│   │   └── crypto.py          # 字段加密
│   ├── scheduler/
│   │   └── quota_scheduler.py # 周期检查生命周期
│   └── config/
│       ├── settings.py
│       └── default_config.yaml
│
├── scripts/                   # 构建/打包脚本
│   ├── build.py
│   └── setup.py
│
└── config/                    # 运行时配置模板
    └── opencode_pool.yaml     # Oh My OpenCode 号池配置模板
```

## 6. 核心流程

### 6.1 新建账号流程

```
1. 用户在前端点击「新建账号」
2. 后端随机生成 GitHub 用户名和密码（保留到 SQLite，后续清理账号时使用）
3. 后端通过 `duckmail.pro` 创建临时邮箱账户
4. 打开 CloakBrowser 可见浏览器窗口（headless=False）
5. 跳转 GitHub 注册页（https://github.com/signup），填写邮箱、密码、用户名
   - 用户名：字母前缀 + 单个连字符 + 数字/字母，如 learner-abc123
   - 密码：随机强密码
   - 操作间加入随机延迟（500ms-2000ms）
6. 点击「Create account」，若页面出现验证码 / 风控 → 暂停流程，弹出人工介入面板
7. 用户完成验证后点击「继续」
8. 后端轮询临时邮箱，读取 GitHub 8 位验证码，逐位填入表单
9. 点击「Continue」；若 GitHub 跳转登录页，使用本流程内存中的生成凭据完成首次登录
10. GitHub 注册成功后立即把可清理凭据加密写入 `pending_accounts`，状态为 `pending_setup`
11. 跳转 OpenCode 登录入口（https://opencode.ai/auth），在 `auth.opencode.ai` 选择 GitHub 登录
12. 在 GitHub OAuth 页面同意 OpenCode Console 授权，等待回调至默认工作区，并从 URL 提取
    `workspace_id`（如 `wrk_xxx`）
13. 跳转 `https://opencode.ai/workspace/{workspace_id}/go`，前端提示用户手动选择 OpenCode Go
    的付款方式并完成 Stripe Checkout；价格和币种以页面实时显示为准
14. 用户支付完成后，在前端点击「已支付」
15. 后端只接受用户在前端点击「已支付」作为支付完成意图，不以 API Key 是否存在判断支付结果
16. 后端跳转 OpenCode 工作台 API Key 页面：`https://opencode.ai/workspace/{workspace_id}/keys`
17. 在页面中定位 `td[data-slot="key-value"]` 单元格，点击复制按钮（`button[data-color="ghost"]`）
18. 读取剪贴板内容，获取完整 API Key（格式：`sk-` + 64 位字符）
19. 按持久化设置决定是否写入 OpenCode 与 Oh My OpenCode 配置；无论是否自动写入，都在单个 SQLite
    事务中把同一 UUID 的 pending 记录提升为完整账号，并保存两类配置应用状态
20. 已写入的号池自动生效；待应用配置可在设置界面统一补写
```

**GitHub 注册表单字段映射**：
- 邮箱：`#email`
- 密码：`#password`
- 用户名：`#login`，只生成字母、数字和单个连字符
- 创建账号按钮：role `button` + accessible name `Create account`
- 验证码输入框：8 个 role `spinbutton` 控件，按页面顺序逐位填写
- 继续按钮：包含 "Continue" 文本的按钮

**CloakBrowser 启动设置**：
- 默认 headed 模式
- 使用 CloakBrowser 自带的 Chromium 指纹实现，不注入自定义页面脚本
- 每个流程创建独立 BrowserContext 和 Page，viewport 固定为 1920x1080
- 不启用代理、`humanize` 或额外指纹参数
- 显式取消 GitHub 注册页默认勾选的附加产品选项
- CAPTCHA、风控、手机号验证和未知阻塞状态仍必须进入人工介入
- 仅 GitHub 首页或仪表盘路径可判定注册完成，其他未知跳转不得推断成功
- 注册成功后 GitHub 清理所需凭据立即加密持久化；流程内存副本在关闭浏览器会话时清空

### 6.2 号池切换流程

号池切换由 **Oh My OpenAgent (OMO)** 的 `runtime_fallback` + `model_fallback` 机制自动完成，本工具只负责维护配置文件。流程如下：

```
1. 当前 OpenCode Go 账号额度用尽（返回 429 / 额度耗尽提示）
2. OMO 根据 oh-my-openagent.json 中的 fallback_models 链自动尝试下一个账号
3. 若所有账号都耗尽，前端提示用户创建新账号
4. 工具可定期刷新账号额度，显示在账号列表中
```

### 6.3 账号失效清理流程

```
1. 用户或额度检查把账号标记为 exhausted / invalid
2. 用户点击删除并重新输入完整 GitHub 用户名，授权自动删除精确目标
3. 后端持久化 requested 删除意图，再使用加密保存的凭据登录 GitHub
4. 后端核对 meta[name="user-login"] 身份并打开删除设置页
5. 后端只在唯一的删除对话框中填写目标用户名和固定短语 `delete my account`，提交后核对 `/users/{username}` sudo 页面
6. 后端填写加密保存的 GitHub 密码并提交；CAPTCHA、2FA、设备验证与未知页面仍由用户处理
7. 只有目标公开资料返回 404 才持久化 remote_deleted
8. 后端从三份 OpenCode / OMO 配置中移除 provider，并在需要时递补编号最小的二级账号
9. 配置成功后，在 SQLite 事务中删除账号记录并更新递补 provider 名称
```

远端已经验证删除但本地配置或 SQLite 清理失败时保留 `remote_deleted`。重试只执行本地清理，不再次
登录或提交远端删除；远端删除验证前取消会删除 `requested` 意图并关闭浏览器。

## 7. 模块设计

### 7.1 前端（Tauri + React）

- **Dashboard**：展示完整与未完成账号、状态（active / exhausted / invalid / pending_setup / pending_payment / cancelled）、当前号池指针和导出按钮。
- **CreateFlow**：新建账号向导，显示权威流程状态和可选的已遮罩截图，弹出人工介入面板。
- **Settings**：配置自动写入开关并应用待处理账号；导出加密包位于 Dashboard。
- **ManualIntervention**：流程暂停时显示步骤说明、可选的已遮罩截图和继续/中止控件；不接收验证码或密码。

### 7.2 后端（Python）

#### 7.2.1 流程引擎 `engine/`

- `FlowSession`：每个新建账号对应一个会话，维护状态机。
- 状态：
  - `idle` → `creating_email` → `github_register` → `manual_verify` → `github_email_verify` → `opencode_login` → `pending_payment` → `fetch_api_key` → `done`
  - 异常：`error` / `cancelled`
- `Step` 抽象：每个步骤可返回 `done`、`need_manual`、`error`。

#### 7.2.2 浏览器层 `browser/`

- `CloakBrowserClient`：单例管理浏览器实例，默认 `headless=False`（便于人工介入）。
- `GitHubRegister`：注册流程、表单填写、验证码检测。
- `OpenCodeLogin`：GitHub OAuth 登录、API Key 读取。
- `PaymentNavigator`：跳转到 OpenCode 支付页。

#### 7.2.3 邮箱 provider `providers/`

```python
class EmailProvider(ABC):
    @abstractmethod
    async def create_email(self) -> str: ...
    @abstractmethod
    async def wait_for_code(self, email: str, timeout: int) -> str: ...
    @abstractmethod
    async def dispose(self, email: str) -> None: ...
```

- 当前只有 `integrations/duckmail.py`，固定连接 `https://duckmail.pro`。
- 保留 `EmailProvider` 接口用于隔离流程与第三方协议，不提供运行时选择或优先级配置。
- provider 随机生成 DuckMail 用户名和密码，密码与访问令牌只在流程内存会话中保留；释放资源时调用
  DuckMail 删除账户接口。

#### 7.2.4 数据层 `storage/`

- SQLite 表结构：见第 8 节。
- 使用 `cryptography` 库对 `github_password`、`api_key` 等字段做字段级 AES-GCM 加密。
- 密钥派生：用户主密码 → PBKDF2 → 加密密钥。

#### 7.2.5 调度器 `scheduler/`

- `browser/opencode_quota`：使用独立无窗口会话登录精确账号，验证 workspace 并读取 Go 页面额度节点。
- `engine/quota_service`：协调单账号和批量后台抓取、状态更新与持久化。
- `quota_scheduler`：按固定间隔顺序执行后台浏览器抓取，并由应用生命周期统一回收。
- 调度器不显示浏览器窗口或主动切换账号；Dashboard 根据已持久化额度生成接近上限提醒。

## 8. 数据模型

### 8.1 核心表

#### accounts

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| uuid | TEXT | 账号唯一标识 |
| github_username | TEXT | GitHub 用户名 |
| github_email | TEXT | 注册邮箱 |
| github_password | BLOB | 加密存储 |
| github_created_at | DATETIME | GitHub 注册时间 |
| opencode_provider_name | TEXT | provider 名称，如 opencode-go3 |
| opencode_workspace_id | TEXT | OpenCode 工作区 ID |
| opencode_api_key | BLOB | 加密存储 |
| opencode_user_id | TEXT | OpenCode 用户 ID |
| email_provider | TEXT | 使用的邮箱服务商 |
| temp_email | TEXT | 临时邮箱地址 |
| status | TEXT | active / exhausted / invalid / pending_payment / cancelled |
| quota_total | INTEGER | 总额度 |
| quota_used | INTEGER | 已用额度 |
| quota_updated_at | DATETIME | 额度更新时间 |
| quota_checked_at | DATETIME | 最近一次确定性额度检查时间 |
| quota_invalid_reason | TEXT | 额度检查确认的失效原因 |
| opencode_configured | INTEGER | OpenCode 配置是否已写入 |
| omo_configured | INTEGER | Oh My OpenCode 配置是否已写入 |
| created_at | DATETIME | 记录创建时间 |
| updated_at | DATETIME | 记录更新时间 |
| notes | TEXT | 备注 |

#### pending_accounts

GitHub 注册完成但尚未取得 OpenCode API Key 的账号独立存放，避免放宽完整账号表的非空约束。其
`github_password` 同样使用字段级 AES-GCM 加密；最终完成时在单个事务中插入 `accounts` 并删除
`pending_accounts`，UUID 保持不变。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| uuid | TEXT | 账号唯一标识 |
| github_username | TEXT | GitHub 用户名 |
| github_email | TEXT | 注册邮箱 |
| github_password | BLOB | 加密存储 |
| github_created_at | DATETIME | GitHub 账号创建时间 |
| email_provider | TEXT | 使用的邮箱服务商 |
| temp_email | TEXT | 临时邮箱地址 |
| status | TEXT | pending_setup / pending_payment / cancelled / invalid |
| created_at | DATETIME | 记录创建时间 |
| updated_at | DATETIME | 记录更新时间 |
| notes | TEXT | 备注 |

#### pool_state

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 单例 |
| current_account_id | TEXT | 当前号池指向的账号 uuid |
| updated_at | DATETIME | 更新时间 |

#### operation_logs

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| account_id | TEXT | 关联账号 |
| level | TEXT | info / warning / error |
| step | TEXT | 当前步骤 |
| message | TEXT | 日志内容 |
| screenshot_path | TEXT | 可选截图路径 |
| created_at | DATETIME | 时间 |

#### settings

| 字段 | 类型 | 说明 |
|------|------|------|
| key | TEXT PK | 配置键 |
| value | TEXT | 配置值 |

#### account_cleanup_operations

| 字段 | 类型 | 说明 |
|------|------|------|
| account_id | TEXT PK / FK | 待清理账号 UUID，账号删除时级联移除 |
| state | TEXT | requested / remote_deleted |
| updated_at | DATETIME | 最近状态更新时间 |

`pending_account_cleanup_operations` 使用相同字段保存未完成账号的清理意图。未完成账号远端删除确认后只
删除对应 pending 本地记录，不修改尚未加入的 OpenCode 号池配置。

数据库 migration v3 创建 `pending_accounts` 与 `pending_account_cleanup_operations`；migration v5 为完整账号
新增两个配置状态字段，现有账号迁移后默认视为已配置。旧数据库按版本顺序升级，不修改既有 migration。

### 8.2 配置项

| key | 默认值 | 说明 |
|-----|--------|------|
| `OPENCODE_REGISTER_SANDBOX_DIR` | 未设置 | 设置后优先覆盖下列全部路径，将 SQLite 与三份配置写入指定沙盒目录 |
| `OPENCODE_REGISTER_DATA_DIR` | 平台应用数据目录 | SQLite 账号库目录 |
| `OPENCODE_REGISTER_AUTH_PATH` | `~/.local/share/opencode/auth.json` | OpenCode 首账号认证配置 |
| `OPENCODE_REGISTER_CONFIG_PATH` | `~/.config/opencode/opencode.json` | OpenCode 二级 provider 配置 |
| `OPENCODE_REGISTER_OMO_PATH` | `~/.config/opencode/oh-my-openagent.json` | OMO fallback 配置 |
| `OPENCODE_REGISTER_QUOTA_CHECK_INTERVAL_SECONDS` | `3600` | 周期额度检查间隔，允许 `60..86400` 秒 |
| `OPENCODE_REGISTER_SCREENSHOTS_ENABLED` | `false` | 显式启用 GitHub 人工介入的已遮罩截图 |
| `OPENCODE_REGISTER_SCREENSHOT_RETENTION_HOURS` | `24` | 活动流程截图最长留存小时数，允许 `1..168` |
| `OPENCODE_REGISTER_SCREENSHOT_MAX_PER_FLOW` | `3` | 每个活动流程最多保留截图，允许 `1..10` |
| `manual_intervention_timeout` | `300` | 当前后端默认人工介入超时秒数 |
| `encryption_salt` | 随机 | SQLite 内生成并保存的加密盐 |

沙盒目录下固定使用 `app-data/accounts.db`、`opencode-data/auth.json`、
`opencode-config/opencode.json` 和 `opencode-config/oh-my-openagent.json`。沙盒模式只隔离本地文件，
不会模拟或绕过 GitHub、DuckMail、OpenCode、人工验证与支付边界。

`auto_switch_pool` 由 OMO 配置负责。后台调度只刷新额度且绝不主动删除账号；只有用户重新输入精确 GitHub 用户名后，
清理流程才自动提交远端删除。安全挑战与未知页面不会自动处理。

界面设置中的 `auto_configure_opencode` 与 `auto_configure_omo` 持久化在 SQLite `settings` 表，默认均为
`true`。Oh My OpenCode 自动配置依赖 OpenCode；关闭 OpenCode 时必须同时关闭 Oh My OpenCode。关闭后
新建或导入账号仍完整加密保存并分配 provider，但对应 `*_configured` 状态为假，且不修改目标配置文件。
重新开启后，用户可点击「应用到现有账号」按 provider 顺序补写；文件冲突时整次操作回滚且不覆盖现有配置。

## 9. 前后端通信

### 9.1 REST API

下表是完整产品的接口清单。当前公开接口包括账号列表与创建、流程控制、账号库、导入导出、额度刷新、
人工确认的失效清理与设置；账号详情和号池手动控制不属于当前公开接口。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/accounts` | 开始新建账号；无请求体，固定使用 DuckMail |
| GET | `/api/accounts/{id}` | 账号详情 |
| DELETE | `/api/accounts/{id}` | 删除账号并清理 GitHub |
| POST | `/api/accounts/{id}/quota/refresh` | 使用后台浏览器刷新单账号额度 |
| POST | `/api/quota/refresh` | 使用后台浏览器顺序刷新全部账号额度 |
| POST | `/api/accounts/{id}/mark-exhausted` | 标记额度用尽 |
| GET | `/api/accounts/{id}/api-key` | 按用户复制操作读取单账号 API Key |
| GET | `/api/accounts/{id}/cleanup` | 获取账号清理流程状态 |
| POST | `/api/accounts/{id}/cleanup/confirm` | 确认已完成人工安全验证并继续自动删除 |
| POST | `/api/accounts/{id}/cleanup/cancel` | 取消尚未确认远端删除的清理流程 |
| GET | `/api/pool` | 号池状态 |
| POST | `/api/pool/next` | 手动切换到下一个账号 |
| POST | `/api/flow/{id}/resume` | 恢复暂停的流程 |
| POST | `/api/flow/{id}/cancel` | 取消流程 |
| POST | `/api/flow/{id}/manual-input` | 提交人工输入 |
| GET | `/api/flow/{id}/screenshot/{screenshot_id}` | 读取该流程拥有的已遮罩 PNG，禁止缓存 |
| GET | `/api/settings` | 读取自动配置开关与待应用数量 |
| PUT | `/api/settings` | 更新自动配置开关 |
| POST | `/api/settings/apply` | 按当前开关为已有账号补写配置 |
| POST | `/api/export` | 导出加密账号包 |
| POST | `/api/import` | 导入加密账号包 |

导出包当前使用 manifest v2，并兼容导入 v1。导入先完整认证并验证包内容，再按目标机器现有号池重新
分配 provider。自动配置开启时按首账号、二级账号和 OMO fallback 写入对应文件；关闭时仅保存账号及待应用
状态，未完成账号始终不写配置。每次配置写入都保留可回滚结果；配置链或 SQLite 整批提交失败时逆序恢复
本次修改，不覆盖既有账号或用户配置。

### 9.2 WebSocket

- `/ws/flow/{id}`：实时推送流程状态和截图标识，不传输图片数据或本地路径。
- `/ws/manual/{id}`：人工介入请求。
- `/ws/logs`：全局日志流，当前尚未实现。

## 10. 人工介入机制

这是整个系统中最关键的设计之一。

### 10.1 触发条件

后端检测到以下情况时，立即暂停流程并请求人工介入：

- 页面出现 CAPTCHA / reCAPTCHA / hcaptcha
- GitHub 要求验证手机号 / 邮箱验证码
- 页面出现未知阻断提示
- 流程步骤超时
- 用户主动点击「暂停」

### 10.2 交互流程

```
后端检测到需要人工介入
    ↓
发送 WebSocket 事件：manual_intervention_required
    ↓
前端弹出 ManualIntervention 面板
    ↓
面板显示：步骤说明 + 可选的已遮罩截图 + 确认控件
    ↓
用户在可见浏览器完成操作后确认继续
    ↓
前端 POST /api/flow/{id}/manual-input
    ↓
后端继续流程
```

### 10.3 浏览器显示策略

- 默认使用 `headless=False`，让用户直接看到浏览器窗口。
- 显式启用后，前端可通过受控 GET 接口展示 GitHub 人工介入点的已遮罩截图。
- 截图默认关闭且付款页面永不捕获；用户始终在可见浏览器窗口中完成验证码和风险验证。

## 11. 号池机制

### 11.1 与 Oh My OpenAgent (OMO) 的集成

OMO 通过 `model_fallback` + `runtime_fallback` 实现多账号自动切换。本工具负责把新账号写入 OMO 所需的三个配置文件，之后 OMO 自动在账号间切换。

**涉及文件**：

| 文件 | 路径 | 作用 |
|------|------|------|
| `auth.json` | `~/.local/share/opencode/auth.json` | 存储每个 provider 的 API Key |
| `opencode.json` | `~/.config/opencode/opencode.json` | 声明 provider 配置、模型列表、baseURL |
| `oh-my-openagent.json` | `~/.config/opencode/oh-my-openagent.json` | 配置每个 agent 的 model 和 fallback_models |

### 11.2 账号命名规则

为避免 `auth.json` 与 `opencode.json` 中的 provider 名称冲突，命名规则如下：

- `auth.json` 中固定为 `opencode-go`（第一个账号）
- `opencode.json` 中从 `opencode-go2` 开始，依次为 `opencode-go3`、`opencode-go4`...
- 第 1 个账号：仅存在于 `auth.json`，名为 `opencode-go`
- 第 2 个及以后：仅存在于 `opencode.json`，名为 `opencode-go2`、`opencode-go3`...

### 11.3 写入 auth.json

`auth.json` 只能存在一个 `opencode-go` provider，因此：

- 第一个账号写入 `auth.json`，provider 名固定为 `opencode-go`
- 后续账号不写入 `auth.json`

```json
{
  "opencode-go": {
    "type": "api",
    "key": "sk-xxx"
  }
}
```

### 11.4 写入 opencode.json

`opencode.json` 中从 `opencode-go2` 开始存放后续账号，**不允许出现 `opencode-go`**（避免与 `auth.json` 中的 `opencode-go` 冲突）。
新增账号时，工具扫描已有的合法编号，使用当前最大序号加一；已有 `opencode-go2` 时自动创建
`opencode-go3`。即使中间存在缺口也不复用旧编号，避免 OMO 中的历史引用意外指向新账号。缺少
`auth.json` 的 `opencode-go` 首账号时停止写入。

```json
{
  "provider": {
    "opencode-go2": {
      "name": "OpenCode Go (Account 2)",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "apiKey": "sk-yyy",
        "baseURL": "https://opencode.ai/zen/go/v1"
      },
      "models": { "kimi-k2.7-code": { "name": "Kimi K2.7 Code" }, ... }
    },
    "opencode-go3": {
      "name": "OpenCode Go (Account 3)",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "apiKey": "sk-zzz",
        "baseURL": "https://opencode.ai/zen/go/v1"
      },
      "models": { "kimi-k2.7-code": { "name": "Kimi K2.7 Code" }, ... }
    }
  }
}
```

**models 自动生成**：
- 工具在写入二级账号前请求 OpenCode Go 官方目录 `https://opencode.ai/zen/go/v1/models`，以响应中的
  `data[].id` 作为当前可用模型集合。
- `/models` 只提供模型 ID、对象类型、创建时间和所有者，无法判断模型实际使用的 AI SDK。工具再从
  OpenCode 使用、由 Anomaly 维护的结构化 `https://models.dev/api.json` 中读取 `opencode-go` 的模型显示名
  和模型级 `provider.npm`，不抓取或解析文档页面。
- 工具只写入两个来源按模型 ID 对齐的严格交集，并只接受 `@ai-sdk/openai-compatible`、
  `@ai-sdk/anthropic` 与 `@ai-sdk/openai`。2026-07-26 的结构化目录中 `grok-4.5` 使用 OpenAI SDK；
  未声明 override 的模型使用 provider 默认的 OpenAI-compatible SDK。
- 已有模型保留用户配置的 `name` 和非传输扩展元数据；`provider` 由结构化目录校正。官网移除的模型从
  所有 `opencode-go2` 及后续 provider 中删除。
- 新账号写入与显式模型刷新都会同步全部二级 provider；任一来源请求失败、响应为空、重复、交集为空或
  结构不合法时，操作安全失败并保留现有配置，不按模型名称猜测 SDK。单一来源暂未同步的模型只从本次
  交集中排除。
- 显式刷新同时移除 OMO fallback 链中已下线模型的引用且不重排剩余项目；若 agent 主模型已经下线，
  则停止刷新并回滚同次 `opencode.json` 变更，不擅自替换用户主模型。
- `auth.json` 中的首账号仍由内置 `opencode-go` provider 管理，不在该文件中写入 `models`。

### 11.5 写入 oh-my-openagent.json

```json
{
  "agents": {
    "build": {
      "model": "opencode-go/kimi-k2.7-code",
      "fallback_models": [
        "opencode-go2/kimi-k2.7-code",
        "opencode-go3/kimi-k2.7-code"
      ]
    }
  },
  "model_fallback": true,
  "runtime_fallback": {
    "enabled": true,
    "max_fallback_attempts": 3,
    "retry_on_errors": [429, 500, 502, 503, 504]
  }
}
```

工具新建账号后，把它加入每个 agent 的 `fallback_models` 链末尾，保留用户现有优先级。每个 agent
优先沿用其现有 OpenCode Go model ID；没有现有 Go 模型时使用 `kimi-k2.7-code`。如果该默认模型已从
官方目录下线，配置写入必须停止并要求更新架构默认值，不得静默选择其他模型。

`oh-my-openagent.json` 不存在时，工具按上例创建最小 `build` agent，以首账号
`opencode-go/kimi-k2.7-code` 为主模型并追加当前二级账号。文件已经存在但 `agents` 显式为空时停止写入，
避免覆盖用户有意设置的结构。

### 11.6 切换策略

- OMO 默认按 `fallback_models` 顺序尝试。
- 当前账号返回 429 / 额度耗尽 / 服务器错误时，自动切换到下一个。
- 工具无需手动切换，只需保证配置文件正确。
- 账号失效时，工具从三个配置文件中移除对应 provider，并清理 GitHub。

### 11.7 自动配置开关

- `自动配置 OpenCode` 控制 `auth.json` 与 `opencode.json` 写入。
- `自动配置 Oh My OpenCode` 控制 `oh-my-openagent.json` 的 fallback 写入，且只能在 OpenCode 开启时启用。
- 两项默认开启，只影响之后新增或导入的账号；关闭不会删除已经写入的配置。
- 未自动写入的账号仍保存完整凭据与预分配 provider，账号列表显示对应待应用状态。
- 「应用到现有账号」只处理待应用账号；预分配 provider 与文件现状不一致时停止并回滚本次全部写入。
- 清理账号时只修改该账号实际写入过的配置，禁止因为删除未配置账号而创建新配置文件。

## 12. 额度检测

> OpenCode 官方没有稳定的额度 API。工具统一使用后台 CloakBrowser 抓取已验证 workspace 的 Go 仪表盘，
> 不使用 API Key 探测或未公开内部端点。

### 12.1 仪表盘抓取

目标 URL：`https://opencode.ai/workspace/{workspace_id}/go`

抓取逻辑：

1. 为额度子系统启动独立 `headless=True` 的 CloakBrowser，账号检查使用隔离 BrowserContext。
2. 使用保存的 GitHub 凭据登录，核对 `meta[name="user-login"]`，通过标准 OAuth 后验证回调 workspace。
3. 进入已验证 Go 页面，等待三个 `[data-slot="usage-item"] [data-slot="usage-value"]` 节点；每个节点必须是
   `0%..100%`，按仪表盘的滚动、每周、每月顺序固定取第三个节点，使用每月用量作为当前展示额度。
4. 若页面明确显示「订阅 Go」入口，则判定当前未订阅或订阅已到期，清空旧额度快照
   并把账号标记为 `invalid`；只读取入口，不点击或尝试付款。
5. 节点缺失、数量错误、格式错误、身份或 workspace 不匹配时安全失败并保留旧快照。
6. CAPTCHA、设备验证和未知阻断不自动处理；返回 `unavailable` 并关闭后台会话。
7. 取得可信用量时写入 SQLite 的 `quota_used` / `quota_total` / `quota_updated_at` 字段。
8. 单账号、批量和周期刷新统一使用此路径；批量与周期任务顺序检查账号，不创建多个前台窗口。

### 12.2 额度检测的用途

- **显示**：在账号列表中展示各账号每月用量百分比。
- **提醒**：当所有已检查账号每月用量均达到 80% 时，提示用户准备创建新账号；未知用量不参与“全部接近上限”判断。
- **不用于主动切换**：切换由 OMO 自动完成，工具只负责刷新数据。

## 13. 安全与隐私

- **主密码**：首次启动时要求用户设置主密码，用于派生加密密钥。
- **空库认证**：SQLite 保存由派生密钥加密的版本化验证值，确保尚无账号时也能认证主密码；验证值
  不包含主密码或派生密钥明文。
- **加密字段**：`github_password`、`opencode_api_key` 必须加密。
- **导出包**：使用独立导出包密码进行 ZIP + AES-256-GCM 认证加密；导入包密码与目标账号库主密码分离。
- **临时邮箱**：使用后尽量释放，避免长期占用。
- **日志**：不记录敏感信息。截图默认关闭，仅捕获已遮罩的 GitHub 人工介入页面，存入权限受限的应用
  私有目录；单张上限 5 MB，默认保留 24 小时且每流程最多 3 张，恢复或进入任一终态时删除，付款页永不截图。
- **GitHub 删除**：用户重新输入精确用户名即授权自动提交；安全挑战仍人工处理，验证远端不存在后才清理本地记录。
- **配置文件安全**：修改 `~/.config/opencode` 和 `~/.local/share/opencode` 前自动备份，避免误操作损坏 OMO。

## 14. 配置说明

```yaml
# default_config.yaml
browser:
  headless: false
  slow_mo: 100
  viewport: "1920x1080"
  anti_detection: true

opencode:
  config_path: "~/.config/opencode/opencode.json"
  auth_path: "~/.local/share/opencode/auth.json"
  omo_path: "~/.config/opencode/oh-my-openagent.json"
  models_url: "https://opencode.ai/zen/go/v1/models"
  models_metadata_url: "https://models.dev/api.json"
  keys_page_url: "https://opencode.ai/workspace/{workspace_id}/keys"
  dashboard_url: "https://opencode.ai/workspace/{workspace_id}/go"

security:
  manual_intervention_timeout: 300

scheduler:
  quota_check_interval: 3600  # 秒
```

## 15. 已交付能力

| 能力 | 内容 |
|------|------|
| 桌面运行时 | Tauri 壳、Python 本地服务与前后端通信 |
| 注册流程 | 临时邮箱 provider、基础流程引擎、GitHub 注册与人工介入面板 |
| OpenCode 接入 | OpenCode 登录、支付跳转与 API Key 读取 |
| 本地存储 | SQLite 加密与号池配置写入（auth.json / opencode.json / oh-my-openagent.json） |
| 账号管理 | 账号列表、导出与导入 |
| 账号维护 | 后台仪表盘额度抓取与失效清理 |
| 应用分发 | PyInstaller + Tauri sidecar 二进制打包与文档 |

## 16. 二进制分发方案

用户要求「分发二进制」，因此采用 **PyInstaller + Tauri sidecar** 方案：

### 16.1 方案

- **Python 后端**：使用 PyInstaller 将 `backend/` 打包成单个可执行文件（`backend` / `backend.exe`）。
- **Tauri sidecar**：将 Python 可执行文件作为 sidecar 资源嵌入 Tauri 应用包。
- **Tauri 启动时**：Rust 启动 Python sidecar 作为子进程，并通过本地 HTTP/WebSocket 与前端通信。
- **前端**：Tauri + React 打包为桌面应用。

### 16.2 打包流程

```
1. 安装依赖：npm ci 与 uv sync --project backend --group dev
2. 冻结后端：uv run --project backend python scripts/build_backend.py
3. 脚本按 rustc 宿主三元组把产物放到 src-tauri/binaries/backend-<target triple>
4. 运行 Tauri 构建：npm run tauri build
5. 输出：.app / .dmg / NSIS 安装包
```

`npm run package` 依次执行第 2 步与第 4 步。

### 16.3 sidecar 命名与目录结构

Tauri 的 `externalBin` 按 `binaries/<name>-<target triple>` 定位 sidecar，因此文件名后缀必须
等于构建所用的 Rust target triple，由 `scripts/build_backend.py` 从 `rustc -vV` 的 `host:`
字段自动推导，不得按操作系统名自行拼装。Windows 追加 `.exe`。

当前支持的三元组：

| 平台 | target triple | sidecar 文件名 |
|------|---------------|----------------|
| macOS Apple Silicon | `aarch64-apple-darwin` | `backend-aarch64-apple-darwin` |
| macOS Intel | `x86_64-apple-darwin` | `backend-x86_64-apple-darwin` |
| Windows x64 | `x86_64-pc-windows-msvc` | `backend-x86_64-pc-windows-msvc.exe` |

```
src-tauri/
├── binaries/                   # 构建产物，不进入版本库
│   └── backend-<target triple>
├── src/
│   └── python_sidecar.rs
...
```

Tauri 把 sidecar 放到应用包中与主可执行文件同级的位置（macOS 为 `Contents/MacOS/`）。
`python_sidecar.rs` 按「`OPENCODE_REGISTER_BACKEND_EXECUTABLE` 显式覆盖 → 与当前可执行文件
同级的 sidecar → 开发期 `backend/.venv`」三级顺序解析启动方式。

### 16.4 注意事项

- 工具不分发 Chromium。CloakBrowser 是纯 Python 包，其浏览器二进制在首次使用时下载到
  `~/.cloakbrowser`，冻结后该路径不变，因此不涉及浏览器二进制的再分发许可问题。开发环境
  可预先运行 `uv run --project backend python -m cloakbrowser install` 提前完成下载。
- PyInstaller 需要 `--paths backend`（后端使用顶层绝对导入），并整包收集 `uvicorn` 与
  `cloakbrowser`：前者通过 `import_from_string` 动态加载协议、事件循环与 lifespan 实现，
  静态分析无法捕获。
- PyInstaller 不支持交叉编译，每个 target triple 必须在对应架构的机器或 CI runner 上构建。
- 当前发布产物**未签名也未公证**：macOS 下载后会被 Gatekeeper 拦截，需用户手动放行；
  Windows 可能触发 Defender 提示。签名与公证尚未交付。

## 17. 已确认问题与设计结论

根据你的回答，以下问题已确认：

1. **用户名/密码**：随机生成，必须保留（加密存储），用于后续清理 GitHub 账号。
2. **支付确认**：用户手动支付，完成后在前端点击「已支付」。
3. **API Key 读取**：从 `https://opencode.ai/workspace/{workspace_id}/keys` 读取「Default API Key」。
4. **额度检测**：OMO 自动切换账号，工具只需通过后台浏览器刷新仪表盘额度用于展示。
5. **本地配置**：OMO 配置涉及 `auth.json`、`opencode.json`、`oh-my-openagent.json` 三个文件。
6. **邮箱验证码**：由工具自行轮询，要求稳定可靠。
7. **GitHub 风控**：按你当前经验，GitHub 注册仅需邮箱和验证码，无额外风控；若遇到手机验证，工具提供人工介入入口。
8. **分发**：二进制分发，采用 PyInstaller + Tauri sidecar。
9. **auth.json 单 provider 规则**：`auth.json` 只能有一个 `opencode-go`，后续账号全部写入 `opencode.json`。
10. **opencode.json 命名规则**：`opencode.json` 中的 provider 从 `opencode-go2` 开始，不出现 `opencode-go`，避免与 `auth.json` 冲突。
11. **workspace_id 获取**：通过 CloakBrowser 登录 OpenCode 后，从最终跳转 URL 中提取默认 workspace_id。
12. **API Key 页面结构**：允许使用 CloakBrowser 在实际浏览器中测试页面结构。
13. **OMO fallback 优先级**：新账号加入 `fallback_models` 链末尾，兼容现有配置，操作前自动备份。
14. **GitHub 删除**：远端账号无需备份；用户重新输入完整用户名后，程序核对精确身份并自动提交删除。
15. **Chromium 下载**：首次启动时自动下载。
16. **临时邮箱支持范围**：`duckmail.pro` 是当前唯一支持的邮箱服务，不提供其他 provider 备选或优先级配置。
17. **OpenCode Go 模型来源**：官方 `/zen/go/v1/models` 决定当前可用 ID，Anomaly 的 Models.dev 结构化
    目录补齐显示名和模型级 AI SDK；两者不一致或失败时保留现有配置。

### 17.1 OpenCode Go 现场契约

2026-07-25 使用真实 Chrome 登录态完成只读与人工边界验证，当前实现以 OpenCode Go 页面为准：

- 登录从 `https://opencode.ai/auth` 开始，重定向到 `https://auth.opencode.ai/authorize`；GitHub
  provider 入口为 `a[href="/github/authorize"]`。
- GitHub OAuth 授权页位于 `github.com/login/oauth/authorize`，授权完成后回调到
  `https://opencode.ai/workspace/{workspace_id}`。
- OpenCode Go 订阅入口为 `https://opencode.ai/workspace/{workspace_id}/go`。未订阅页面提供
  「订阅 Go」与「其他付款方式」；Alipay 会进入 Stripe Checkout。自动化只负责导航和等待，
  不点击 Checkout 的最终「订阅」按钮。
- 现场价格为首月 US$5、之后 US$10/月，但价格属于第三方实时产品数据，流程不得硬编码金额、
  币种或折扣，也不得使用价格判断付款完成。
- 新工作区在付款前已经存在 `Default API Key`。因此密钥存在、复制成功或格式有效均不能证明
  OpenCode Go 已付款；只有用户明确点击「已支付」后，流程才进入密钥读取步骤。
- API Key 表格的 `data-slot` 与 `data-color` 选择器仍与第 19 节一致。复制按钮当前还带有
  `title="复制 API 密钥"`，但实现以已验证的结构化选择器为主。

**变更原因**：原登录路径与固定 35 元描述已不符合当前 OpenCode Go 产品，继续实现会把流程导航到
不存在的入口，并错误地把默认密钥当成付款凭据。

**边界影响**：浏览器层新增 `auth.opencode.ai` 允许主机和 OpenCode Go 页面状态识别；流程引擎仍把
支付作为人工确认状态，前端与 API 不接收支付凭据。

**迁移影响**：当前没有相关持久化数据或客户端迁移；旧设计中的 `/login`、固定金额与
密钥存在即付款完成的假设必须整体移除，不提供兼容分支。

**测试影响**：自动测试使用 fake browser 覆盖 OAuth 回调、未知主机、待支付、支付确认、密钥复制
失败和密钥格式校验，不执行真实 OAuth、付款或密钥读取。真实流程只允许受控人工验证。

**回滚考虑**：仅当 OpenCode Go 再次改变公开页面契约，并通过新的受控现场验证确认后，才能成套
更新登录、付款、密钥选择器、流程状态、文档和测试；不得单独恢复旧 URL 或旧价格。

### 17.2 官方模型目录与 OMO 顺序决策

2026-07-25 核对 OpenCode Go 官方文档及其公开源码后，当前实现使用以下配置契约：

- 官方文档提供 `GET https://opencode.ai/zen/go/v1/models` 作为当前模型 ID 列表来源；Models.dev 的结构化
  元数据当前可能为模型声明 `@ai-sdk/openai-compatible`、`@ai-sdk/anthropic` 或 `@ai-sdk/openai`。
- 官方路由返回 OpenAI 风格的 `{ "object": "list", "data": [...] }`，每项包含 `id`、`object`、
  `created` 和 `owned_by`；服务端已经过滤 `alpha-` 模型，但响应不包含显示名、端点或 AI SDK。
- OpenCode 使用的 Models.dev 已归 Anomaly 维护，其 `api.json` 为 `opencode-go` 提供结构化显示名及模型级
  `provider.npm`；工具只使用它与官方 ID 列表的严格交集，不解析 MDX 文档表。发布时差导致仅出现在
  单一来源的模型会暂时排除，交集为空时失败关闭，不猜测显示名或 SDK。
- 二级账号新增前获取并合并两个目录，在同一次 `opencode.json` 原子写入中同步现有二级 provider。
- 二级账号序号由配置写入器按现有最大序号加一自动分配，不依赖调用方传入编号。
- 新账号追加到每个 OMO agent 的 `fallback_models` 链末尾，不重排或覆盖已有 fallback。
- OMO 更新失败时回滚同次 `opencode.json` 变更，避免形成只有 provider、没有 fallback 的部分状态。

**变更原因**：原设计同时描述了内置模型列表、复制已有模型和链首/链末两种 fallback 顺序，无法形成
确定且可更新的实现；同时，官方 `/models` 响应不足以区分 Anthropic 与 OpenAI-compatible 模型。双源
结构化校验补齐协议元数据，用户确认新账号采用链末追加。

**边界影响**：`config/` 的模型目录适配器固定访问 OpenCode Go 与 Models.dev 两个预期 HTTPS 主机，
响应在边界转换为类型化模型，配置写入器仍只接收已验证模型，不接受运行时任意 URL 或 SDK 名称。

**迁移影响**：现有 `opencode-go2` 及后续 provider 在下一次新增账号或显式刷新时同步官方模型 ID；
仍可用模型的自定义名称和非传输扩展元数据保留，模型级 `provider` 按结构化目录纠正，已从官网移除的
模型及其 fallback 引用删除。其余 OMO fallback 顺序不变，仅在末尾追加；已下线的 agent 主模型需要
用户明确选择替代项。

**测试影响**：自动测试使用 fake HTTP 覆盖双源合并、发布时差、空交集、未知 SDK、重复、畸形和上游失败响应，
并使用临时目录覆盖 provider override 校正、自动编号、链末追加、幂等、备份、跨文件回滚和未知默认
模型，不访问开发者真实 OpenCode 配置。

**回滚考虑**：若任一目录删除或改变契约，停止模型同步并保留现有配置；只有核实新的官方契约后才能
成套更新客户端模型、配置同步逻辑、架构文档和测试，不回退到抓取文档页面、按名称猜测或静默使用
硬编码旧列表。

### 17.3 额度与账号清理契约

- **原因**：OpenCode 没有稳定的公开额度 API，未公开 usage 路径也不能作为产品契约；GitHub 远端删除不可逆。
  当前实现因此采用“后台认证浏览器读取已验证仪表盘 DOM”和“记录精确删除授权、自动提交、远端验证后本地清理”。
- **边界影响**：额度子系统拥有独立 `headless=True` 浏览器，单账号、批量和周期刷新统一创建隔离上下文且不显示
  前台窗口。标准 GitHub OAuth 只在预期主机、路径和唯一启用的 `Authorize` 按钮上继续；CAPTCHA、设备验证和
  未知页面不自动处理，检查关闭会话并保留旧快照。额度检查只读取三个精确 `data-slot` 百分比，固定持久化第三个
  每月用量节点，并读取「订阅 Go」入口，
  不点击订阅或支付控件。GitHub 清理只在用户重新输入完整用户名后操作现场验证过的删除对话框和 sudo 页面；任何控件不唯一、
  身份不匹配或页面结构未知都安全暂停，不猜测选择器或绕过验证。
- **迁移影响**：SQLite schema version 2 新增 `account_cleanup_operations`，以 `requested` 和
  `remote_deleted` 区分不可逆边界；取消只删除 `requested` 状态，已经验证的远端删除必须继续完成本地清理。
  订阅状态识别不需要 schema migration；账号下次显式浏览器检查确认无有效订阅时会清除旧额度快照。
- **测试影响**：fake browser 覆盖三窗口 DOM、无效登录、标准 OAuth、workspace 不匹配、节点格式失败、未订阅入口、
  旧额度清除、后台会话关闭和安全挑战；GitHub 清理测试覆盖双确认字段、sudo 密码、远端 404、安全挑战、重试和取消。临时目录覆盖三配置文件删除、主账号递补、跨文件回滚和 SQLite 原子删除，
  自动测试不访问真实账号或用户配置。
- **回滚考虑**：仪表盘 `data-slot` 契约变化时保留现有额度并返回不可用，不回退到 API Key 推理探测、未公开端点
  或递归扫描未知页面脚本。
  若订阅入口的可访问名称变化，则停止判定并保留现有额度，直到重新完成受控现场验证；不按价格或任意页面文本猜测。
  远端已删除而本地清理失败时保留 `remote_deleted`，下次重试不再登录 GitHub；不得通过回滚数据库状态假装
  远端账号仍存在。

### 17.4 DuckMail 单 Provider 决策

- **原因**：当前产品只需要 `duckmail.pro`，删除未使用实现可缩小外部主机、配置和凭据处理范围。
- **边界影响**：provider 抽象仍保留；工厂、优先级选择和 `POST /api/accounts` 的 provider 请求字段被删除。
- **迁移影响**：当前阶段没有已发布的 provider 设置或数据库迁移；旧客户端需改为无请求体创建流程。
- **测试影响**：使用 mock HTTP 覆盖 DuckMail 注册、轮询、过滤、删除、畸形响应和超时，不自动执行真实删号。
- **回滚考虑**：回滚必须成套恢复 provider 实现、工厂、类型、API/前端契约、文档和测试，避免部分兼容状态。

### 17.5 未完成账号、安全截图与导入恢复决策

- **未完成账号**：GitHub 注册成功是可清理身份的不可逆边界，必须立即加密写入独立
  `pending_accounts`；取得完整 OpenCode 数据后按自动配置设置执行文件写入，再以同一 UUID 原子提升。
  自动配置关闭不是失败，完整账号保存待应用状态；失败、取消和待付款状态仍可恢复清理。
- **安全截图**：截图必须显式启用，只能捕获已遮罩的 GitHub 人工介入页面，并受私有权限、
  5 MB 单文件上限、时间留存、每流程数量和终态删除约束；事件只公开随机标识，付款页禁止截图。
- **导入恢复**：账号包是跨机器恢复载体，不沿用源机器 provider 排位。目标机器按现有号池和自动配置
  开关重建配置或保存待应用状态，SQLite 批次与配置补偿共同保证失败不留下本次部分写入；pending 记录不进入号池。
- **测试影响**：临时 SQLite 与沙盒配置覆盖加密 pending、稳定 UUID 提升、远端清理、截图遮罩/权限/留存、
  v1/v2 bundle、provider 重排和逆序回滚。自动测试不读取或修改真实 OpenCode 配置。

### 17.6 自动配置可选策略

- **原因**：桌面用户可能只需要本地账号管理，不希望应用自动修改现有 OpenCode 或 Oh My OpenCode 配置。
- **边界影响**：SQLite 设置保存两个默认开启的开关；Oh My OpenCode 依赖 OpenCode。账号完成、导入、补配置和
  清理共用每账号配置状态，未配置账号的删除不会触碰对应文件。
- **迁移影响**：schema version 5 增加 `opencode_configured` 与 `omo_configured`，历史账号默认值为真；设置表
  未存在开关键时使用真，不改变现有用户行为。
- **测试影响**：临时数据库和沙盒路径覆盖默认值、依赖校验、关闭后入库、仅 OpenCode 写入、补配置回滚、
  导入状态和未配置账号清理；前端覆盖开关联动、锁库状态和待应用操作。
- **回滚考虑**：可移除设置入口并保持开关为真，但不得删除状态列或把待应用账号误标为已写入；回滚前必须
  先显式应用或导出仍待配置的账号。

## 18. workspace_id 自动提取方案

OpenCode 登录后通常会重定向到默认工作区：

```
https://opencode.ai/workspace/{workspace_id}/...
```

### 提取流程

1. CloakBrowser 完成 GitHub OAuth 登录后，等待页面跳转。
2. 监听 `page.url()` 变化，匹配 `/workspace/([a-zA-Z0-9_]+)/` 正则。
3. 提取 `workspace_id` 并保存到 SQLite。
4. 后续 API Key 读取使用：
   ```
   https://opencode.ai/workspace/{workspace_id}/keys
   ```

若 URL 不符合受支持的 HTTPS 主机、路径和 `wrk_` 标识格式，流程安全失败或进入人工介入；不访问猜测的
工作区列表路径，也不扫描未知隐藏字段。

## 19. API Key 页面读取策略

根据 OpenCode 源码分析，API Key 页面结构如下：

| 元素 | 选择器 |
|------|--------|
| Keys 导航链接 | `a[data-nav-button][href*="/keys"]` |
| 创建 API Key 按钮 | `section button[data-color="primary"]` |
| API Key 表格 | `table[data-slot="api-keys-table-element"]` |
| Key 名称单元格 | `td[data-slot="key-name"]` |
| Key 值单元格 | `td[data-slot="key-value"]` |
| 复制按钮 | `td[data-slot="key-value"] button[data-color="ghost"]` |
| 显示文本（截断） | `td[data-slot="key-value"] span` |

### 读取完整 API Key 的方法

完整 key 不在 DOM 文本中，需要通过以下方式获取：

1. **定位「Default API Key」行**：
   ```python
   row = page.locator('tr:has(td[data-slot="key-name"]:text-is("Default API Key"))')
   copy_button = row.locator('td[data-slot="key-value"] button[data-color="ghost"]')
   ```

2. **点击复制按钮 + 读取剪贴板**：
   ```python
   await copy_button.click()
   api_key = await page.evaluate('navigator.clipboard.readText()')
   ```

3. **备用方案**：若剪贴板读取失败，弹出人工介入面板，让用户复制粘贴。

4. **失败回退**：如果页面上没有「Default API Key」，提示用户创建。

## 20. 当前交付边界

上述实现与关键细节已经确认并由自动化门禁覆盖。

二进制分发已交付：PyInstaller 冻结的 Python sidecar、Tauri `externalBin` 嵌入、Rust 侧三级启动
解析、macOS 与 Windows 的发布矩阵与校验和产物。CloakBrowser Chromium 不再分发，由运行时下载，
因此不存在待解决的再分发许可问题。

当前仍未交付**代码签名与 macOS 公证**。发布产物为未签名状态，
`docs/rules/12-collaboration-and-release.md` §12.8 中签名/公证两项仍未满足，不得表述为已完成。
