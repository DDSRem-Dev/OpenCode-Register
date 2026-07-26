# 本地服务 API

本地 FastAPI 服务只监听 `127.0.0.1:17891`，所有 REST 路径使用 `/api` 前缀。完整机器可读契约由运行时的 `/api/openapi.json` 提供。

`GET /api/health` 的 `storage_mode` 为 `system` 或 `sandbox`。沙盒模式由
`OPENCODE_REGISTER_SANDBOX_DIR` 启用，供前端明确提示当前本地文件写入边界。

## Phase 6 账号库

账号库在每次进程启动后保持锁定。首次初始化要求用户输入并确认主密码；后续启动使用同一主密码解锁。
数据库保存由派生密钥生成的 AES-GCM 认证密文，因此即使账号库为空也能拒绝错误主密码。主密码和
派生密钥只驻留当前进程内存，不写入文件或响应。

| 方法 | 路径 | 请求 | 成功响应 |
| --- | --- | --- | --- |
| `GET` | `/api/vault` | 无 | `{ "unlocked": bool, "initialized": bool }` |
| `POST` | `/api/vault/unlock` | 主密码；首次初始化还需确认值 | 账号库状态 |
| `GET` | `/api/accounts` | 无 | 不含密码、API Key 或完整邮箱的账号摘要列表 |
| `POST` | `/api/accounts` | 无；账号库必须已解锁 | 异步账号创建流程快照 |
| `POST` | `/api/export` | 至少 12 字符的导出包密码 | `application/vnd.opencode-register.bundle` 二进制文件 |
| `POST` | `/api/import` | 导出包密码和 Base64 编码文件 | `{ "imported_count": int }` |

账号列表只返回显示所需字段。`github_email_masked` 在服务边界完成脱敏；`github_password` 与 `opencode_api_key` 不属于列表响应模型。
用户点击复制按钮时，`GET /api/accounts/{id}/api-key` 才按精确账号解密并返回 API Key，前端校验格式后立即写入
系统剪贴板，不渲染或缓存密钥；专用响应使用 `Cache-Control: no-store`。
GitHub 注册完成后即创建状态为 `pending_setup` 的未完成账号；其 `opencode_provider_name` 和
`opencode_workspace_id` 为 `null`，额度操作不可用，但仍可执行经确认的 GitHub 清理。最终取得 API Key 时，
同一 UUID 在单个 SQLite 事务中提升为完整账号。
账号库未解锁时，`POST /api/accounts` 返回 `423 vault_locked`，不会启动外部注册流程。
流程取得完整 OpenCode 数据并完成 SQLite 加密持久化后进入 `done`。自动配置开启时文件写入失败仍会阻止
完成；关闭时账号保存为待应用配置。公开快照只返回分配的 `opencode_provider_name`，不返回任何凭据。

## 自动配置设置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings` | 读取两个开关和两类待应用账号数量 |
| `PUT` | `/api/settings` | 原子更新自动配置 OpenCode 与 Oh My OpenCode 开关 |
| `POST` | `/api/settings/apply` | 为已有账号补写当前已开启的配置，要求账号库已解锁 |

两个开关默认开启。`auto_configure_omo=true` 要求 `auto_configure_opencode=true`；关闭 OpenCode 时客户端同步
关闭 Oh My OpenCode。账号摘要通过 `opencode_configured` 与 `omo_configured` 表示实际文件应用状态。
补配置校验预分配 provider，冲突或任一文件失败时回滚本次写入，不在响应中返回路径、密钥或文件内容。

## 加密包格式

扩展名为 `.ocrbundle`。文件由以下部分顺序组成：

```text
OCRB1 | 16-byte salt | 12-byte nonce | AES-256-GCM ciphertext and tag
```

密文内容是一个只允许 `manifest.json` 和 `accounts.json` 的 ZIP。当前导出清单版本为 2，并兼容导入版本 1；
清单包含格式版本、UTC 创建时间、账号数量和账号载荷 SHA-256。版本 2 可包含完整账号和未完成账号，后者
不包含 OpenCode 凭据。AES-GCM 使用 PBKDF2-SHA256 从导出包密码派生密钥，并认证版本前缀。

导入在写入前完成文件大小、认证标签、ZIP 条目、路径、符号链接、解压大小、清单版本、摘要、字段模型和
包内唯一性校验。完整账号按目标机器现有账号重新分配 provider，并遵循目标机器当前自动配置开关；关闭的
配置保存为待应用，未完成账号不写 OpenCode 配置。每次文件写入都保留备份，任一配置失败或 SQLite 整批
提交失败时按逆序回滚本次配置变更。UUID 或 provider 冲突不会覆盖已有账号。

## 稳定错误

错误响应统一使用：

```json
{
  "code": "stable_machine_code",
  "message": "可安全展示的消息",
  "details": null
}
```

Phase 6 使用的主要错误码包括 `vault_locked`、`invalid_master_password`、
`master_password_confirmation_mismatch`、`invalid_import_bundle`、`account_import_conflict` 和
`account_import_configuration_failed`。错误响应不会包含密码、账号密文、完整邮箱、导入包内容或本地路径。

## Phase 3 安全截图

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/flow/{flow_id}/screenshot/{screenshot_id}` | 返回流程拥有的已遮罩 `image/png`，并设置 `Cache-Control: no-store` |

截图默认关闭，WebSocket 快照仅返回 `screenshot_id`，不传图片内容或本地路径。标识不存在、已过期、已随
流程终态删除或不属于指定流程时统一返回 `404 flow_screenshot_not_found`。

## Phase 7 额度检测

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/accounts/{id}/quota/refresh` | 使用后台浏览器刷新指定账号额度 |
| `POST` | `/api/quota/refresh` | 使用后台浏览器顺序刷新全部账号额度 |
| `POST` | `/api/accounts/{id}/mark-exhausted` | 按用户明确操作将指定账号标记为额度耗尽 |
| `GET` | `/api/accounts/{id}/api-key` | 按用户明确复制操作读取指定账号 API Key |

单账号和批量刷新返回 `updated`、`exhausted`、`invalid` 或 `unavailable`。每次检查创建独立的无窗口
CloakBrowser 上下文，登录保存的精确 GitHub 身份，只在预期主机、路径和唯一启用的 `Authorize` 按钮上继续，
并验证 OAuth 回调 workspace。检查结束立即关闭上下文，不向前台显示浏览器窗口。

后台浏览器进入已验证的 `/workspace/{workspace_id}/go` 页面后，严格读取三个
`[data-slot="usage-item"] [data-slot="usage-value"]` 百分比，并按滚动、每周、每月的页面顺序固定使用第三个
每月用量值。如果页面明确显示「订阅 Go」入口，
则识别为未订阅或订阅已到期，清除旧额度快照并返回 `invalid`。检测只读取订阅入口，不点击按钮、选择付款方式
或推断付款结果；节点数量、格式或页面结构不明确时返回 `unavailable` 并保留旧快照。

CAPTCHA、设备验证和未知安全阻断不会被自动处理或绕过。此类状态返回 `unavailable`，关闭本次后台会话并保留
已有额度；后续调度可以重新尝试。

确定账号失效时，账号摘要保存 `quota_checked_at` 和枚举化的 `quota_invalid_reason`。界面显示检查日期与原因，
不再显示空月度进度；历史失效记录迁移后使用原更新时间，并明确标记为历史原因未知。

## Phase 7 账号清理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `DELETE` | `/api/accounts/{id}` | 请求体携带精确 `confirmed_username`，启动 GitHub 清理流程 |
| `GET` | `/api/accounts/{id}/cleanup` | 读取当前账号清理快照 |
| `POST` | `/api/accounts/{id}/cleanup/confirm` | 用户完成 CAPTCHA、2FA 或设备验证后继续自动删除 |
| `POST` | `/api/accounts/{id}/cleanup/cancel` | 在远端删除确认前取消清理 |

用户重新输入完整 GitHub 用户名即授权删除精确目标。后端登录并核对 `meta[name="user-login"]`，只在现场验证过的
删除对话框中填写用户名和固定短语 `delete my account`，再于 `/users/{username}` sudo 页面填写加密保存的密码并提交。
CAPTCHA、2FA、设备验证和未知页面结构仍暂停给用户。只有公开资料返回 `404` 后，后端才记录 `remote_deleted`，随后更新 `auth.json`、`opencode.json`、
`oh-my-openagent.json` 并删除 SQLite 账号。删除首账号时递补编号最小的二级账号；本地清理失败可依据持久化状态重试，
不会再次登录已经删除的 GitHub 账号。
