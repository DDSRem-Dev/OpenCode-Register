# OpenCode Go 登录、付款与 API Key

GitHub 登录会话随后执行 OpenCode Console OAuth，导航到 OpenCode Go
订阅页面，等待用户手动付款，然后读取默认 API Key。页面契约于 2026-07-25 使用真实 Chrome
登录态验证；自动测试不会执行真实 OAuth、付款或密钥复制。

## 已验证页面契约

```text
https://opencode.ai/auth
  -> https://auth.opencode.ai/authorize
  -> /github/authorize
  -> https://github.com/login/oauth/authorize
  -> https://opencode.ai/workspace/{workspace_id}
  -> https://opencode.ai/workspace/{workspace_id}/go
```

OpenCode Go 未订阅页面显示「订阅 Go」和「其他付款方式」。Alipay 路径会进入 Stripe Checkout；
后端只打开 `/go` 页面，不选择付款方式，也不点击 Checkout 的最终「订阅」按钮。金额、币种和
折扣属于实时第三方数据，不进入流程判断。

单账号额度浏览器检查会在已验证的 workspace Go 页面读取「订阅 Go」入口。该入口存在时说明
当前没有可用订阅，检查结果标记账号失效并清空旧额度快照；检测不会点击订阅入口。未知页面或不唯一状态
仍安全失败，不按价格、API Key 是否存在或页面任意文本推断订阅有效性。

默认密钥页面为 `https://opencode.ai/workspace/{workspace_id}/keys`。实现定位
`Default API Key` 行中的 `td[data-slot="key-value"] button[data-color="ghost"]`，点击后读取
剪贴板，并只接受 `sk-` 加 64 位字母数字字符。隔离 BrowserContext 仅为
`https://opencode.ai` 预授予剪贴板读写权限，避免浏览器权限提示阻塞读取；读取超过 5 秒或格式
校验失败时，流程请求用户手动复制。API 使用 `SecretStr` 接收并在边界再次校验。

## 状态机

```text
github_email_verify -> opencode_login -> pending_payment -> fetch_api_key -> done
                              \-> manual_verify ---------/
                              \-> error
```

- `opencode_login`：执行 OpenCode Auth 与 GitHub OAuth，只接受已验证的 HTTPS 主机。
- `pending_payment`：已打开 OpenCode Go 页面，等待用户在可见浏览器中亲自付款。
- `fetch_api_key`：用户明确点击「已支付」后，读取默认 API Key。
- `manual_verify`：OAuth 未知页面或密钥自动复制失败时暂停。
- `done`：密钥已通过格式校验，号池配置写入与 SQLite 加密持久化均已完成。任一操作失败时
  流程进入 `error`，配置文件写入会按本次备份回滚。

新工作区在付款前可能已经存在默认密钥，因此密钥存在或可复制不能证明付款成功。流程只接受
用户的明确付款确认作为继续意图，不自动检测、伪造或绕过付款结果。

## 秘密边界

- API Key 明文只存在于浏览器适配器、流程私有 `SecretStr` 和可选人工提交请求中。
- `FlowSession`、HTTP 响应和 WebSocket 事件只公开 `api_key_captured`，不包含密钥。
- 完成边界成功后立即清除流程内存中的 GitHub 密码和 API Key；长期凭据仅保存在加密账号库
  及 OpenCode 自身要求的本地配置文件中。
- 前端人工输入使用密码字段，不写入浏览器存储，提交后立即清空组件状态。
- 未知主机、OAuth 阻断、剪贴板异常和页面结构变化均返回类型化人工或错误状态，不包含页面原文。

## 自动验证

Fake browser 测试覆盖真实路由顺序、GitHub OAuth 授权、workspace ID 提取、未知主机拒绝、
付款人工状态、来源限定的剪贴板权限、默认密钥选择器、读取超时、剪贴板校验和人工密钥回退。
端到端真实付款仍必须人工验证，且不得作为 CI 测试执行。
