# Temp-Mail 邮箱 Provider

Python 后端提供统一的异步邮箱接口。工作流只依赖
`backend/providers/base.py` 中的 `EmailProvider`；Temp-Mail 的页面结构、浏览器会话和邮件内容不会离开
`browser/` 与 `providers/` 边界。

## 支持范围

当前唯一支持的邮箱 provider 是 `temp_mail`，页面地址固定为 `https://temp-mail.org/en/`。
应用不接收 provider 名称、优先级、自托管地址或第三方 provider 凭据。

`TempMailBrowser` 与 `TempMailProvider` 共同实现以下操作：

- `create_email()`：在独立 CloakBrowser 上下文中打开 Temp-Mail，从唯一的只读 `#mail` 控件读取并校验地址。
- `wait_for_code(email, timeout)`：点击页面唯一的 `Refresh` 控件刷新同一上下文的收件箱，不执行整页刷新；
  只打开发件地址以 `@github.com` 结尾的邮件，
  从主题和正文提取 GitHub 八位数字验证码。
- `dispose(email)`：关闭该邮箱的浏览器上下文并清除 provider 内存引用。Temp-Mail 邮箱由站点按其生命周期
  自动失效，应用不保留 cookie、令牌或邮箱内容。

Temp-Mail 与 GitHub/OpenCode 使用不同的浏览器上下文，邮箱页面导航不会覆盖注册页面。轮询可通过取消调用方的
异步任务终止；每次点击刷新后为页面保留 5 秒加载窗口，空收件箱再等待 5 秒才进入下一轮，避免连续点击中断
站点加载。连续页面读取失败和总等待时间都有明确上限。

## 浏览器与协议边界

所有 Temp-Mail 选择器位于 `backend/browser/temp_mail.py`。适配器只允许无端口、无用户信息的
`https://temp-mail.org/en/` 页面及其 `/en/` 子路径，并在导航和打开邮件后重新校验主机。页面返回的地址会
立即规范化，邮件会转换为 Pydantic `TempMailMessage`，原始 DOM、Page 和 Locator 不进入流程层。
收件箱首行是站点保留的隐藏模板；适配器只接受带 `data-mail-id` 的实际邮件链接，并从
`.inbox-data-content-intro` 读取打开后的正文。

收件箱最多检查 50 封邮件。页面适配器与 provider 都校验 GitHub 发件域，非 GitHub 邮件即使包含八位数字也不会
被接受。验证码不进入 `FlowSession`、HTTP、WebSocket、日志、截图或持久化模型。

## 测试与验证

单元测试使用 fake Page、Locator 和 `TempMailMailboxClient` 覆盖邮箱读取、邮件打开、发件人过滤、验证码提取、
主机拒绝、超时和会话释放，不访问真实 Temp-Mail，也不创建 GitHub 账号。线上页面契约仅允许受控人工验证。

## 架构变更说明

本次变更删除 DuckMail HTTP 实现和响应模型，改为 Temp-Mail 浏览器实现。provider 抽象、无请求体的
`POST /api/accounts` 和单 provider 策略保持不变；数据库不需要迁移，历史账号中的 `email_provider` 仅作为
来源记录保留。回滚必须同时恢复浏览器适配器、provider、模型、服务构造、测试和文档。
