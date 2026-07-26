# DuckMail 邮箱 Provider

Python 后端提供统一的异步邮箱接口。工作流只依赖
`backend/providers/base.py` 中的 `EmailProvider`，DuckMail 响应、令牌和协议差异不会离开
`providers/` 边界。

## 支持范围

当前唯一支持的邮箱 provider 是 `duckmail`，服务地址固定为 `https://duckmail.pro`。
应用不再接收 provider 名称、优先级、自托管地址或第三方 provider 凭据。

`DuckMailProvider` 实现以下操作：

- `create_email()`：调用 `POST /api/auth/register`，随机生成用户名、密码和显示名称，并返回规范化的
  `@duckmail.pro` 地址。
- `wait_for_code(email, timeout)`：调用 `GET /api/emails?folder=inbox&limit=50`，只检查目标收件地址和
  `@github.com` 发件人，并提取 GitHub 八位数字验证码。
- `dispose(email)`：调用 `DELETE /api/auth/account`，用当前会话的 Bearer 令牌和账户密码尽力删除账户，
  随后清除本地会话。

邮箱密码和访问令牌只保存在 provider 实例的内存会话中，不进入流程快照、日志、异常、模型 `repr`
或前端。轮询可通过取消调用方的异步任务终止；连续请求失败和总等待时间都有明确上限。

## 协议边界

配置模型位于 `backend/providers/models.py`，未知字段会被拒绝。provider 只允许无端口、路径、查询参数、
用户信息或片段的 `https://duckmail.pro` 源地址。第三方 JSON 会立即转换为 Pydantic 模型，畸形注册或
邮件列表响应会产生净化后的 `EmailProviderResponseError`，不会传递原始响应或邮件正文。

注册响应必须同时满足以下条件：

- `user.username` 与本次随机生成的用户名一致；
- `user.email` 等于该用户名对应的 `@duckmail.pro` 地址；
- `token` 非空。

删除操作只针对当前 provider 实例中与邮箱地址精确匹配的会话。它属于流程资源回收；显式面向用户的
远端账户删除功能仍需遵守架构中的人工确认边界。

## 测试与验证

单元测试使用 `httpx.MockTransport` 覆盖注册、收件箱查询、验证码筛选、账户删除、畸形响应、主机校验和
超时，不访问真实 DuckMail 服务，也不创建或删除真实账户。线上协议可用性需要受控人工验证。

## 架构变更说明

本次变更删除 CloudMail、Cloudflare、MailNest、YYDS 和 Mail.tm 兼容实现，同时删除 provider 工厂和
优先级请求字段。原因是产品当前只允许 DuckMail，保留多 provider 选择会扩大凭据和外部主机的攻击面。

该变更不修改当前数据库结构；尚未落地的旧 provider 优先级设置不再实施。REST 创建流程请求不再
接受 provider 配置，因此旧客户端必须改为发送无请求体的 `POST /api/accounts`。回滚需要同时恢复旧实现、
类型模型、工厂、API 请求字段、前端调用和对应测试，不能只恢复其中一层。
