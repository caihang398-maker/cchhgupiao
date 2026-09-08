# Creem 订阅测试与部署

本功能先用于小范围测试支付链路。系统只有在收到 Creem 的签名
`subscription.paid` 回调并核对商品、金额、币种、订单和会员后，才会延长服务期。
用户从支付页返回系统不代表已经付款。

## 能力边界

- Creem 结账目前支持银行卡、Apple Pay 和 Google Pay，具体展示取决于地区和设备。
- Creem 可能向符合条件的中国个人商户提供支付宝结算，但这是平台向商户打款，
  不是买家在结账页使用支付宝付款。
- 是否允许个人入驻、是否需要补充资料及能否正式收款，以 Creem 的账户审核结果为准。
- 测试模式不产生真实扣款。未完成审核和真实小额验证前，不要开启正式模式。

官方资料：

- https://docs.creem.io/getting-started/test-mode
- https://docs.creem.io/code/webhooks
- https://docs.creem.io/merchant-of-record/finance/payment-methods
- https://docs.creem.io/merchant-of-record/finance/payouts

## 1. 升级 MySQL 5.7

先备份 `stock_quant_saas`，再执行：

```powershell
& "E:\mysql-5.7.36-winx64\bin\mysql.exe" `
  --default-character-set=utf8mb4 `
  -uroot -p `
  --execute="source E:/stock-quant/database/migrations/20260729_creem_subscriptions.sql"
```

MySQL 安装目录不同的服务器需要替换 `mysql.exe` 路径。迁移脚本可重复执行。

## 2. 配置测试环境

在服务器“系统环境变量”中设置以下值，不要写入 Git：

```text
AUTH_ENABLED=true
PAYMENT_ENABLED=true
CREEM_MODE=test
CREEM_TEST_API_KEY=测试API密钥
CREEM_TEST_WEBHOOK_SECRET=测试回调密钥
CREEM_LIVE_ENABLED=false
CREEM_SUCCESS_URL=https://你的域名/subscription
PAYMENT_WEBHOOK_HOST=127.0.0.1
PAYMENT_WEBHOOK_PORT=8511
```

修改系统环境变量后，需要重新启动 Web 和支付回调计划任务。

## 3. 配置套餐商品

1. 在 Creem 测试环境创建订阅产品并记录产品编号、价格最小单位和币种。
2. 登录系统管理员账号，进入“账号管理”中的“Creem订阅”。
3. 将系统套餐与 Creem 测试产品编号一一对应。
4. 商品金额、币种和服务月数必须与系统套餐一致。

不要把正式产品编号配置到测试环境，也不要把测试产品编号用于正式环境。

## 4. 安装回调服务

以管理员身份运行：

```powershell
cd E:\stock-quant
powershell -ExecutionPolicy Bypass -File scripts\install_payment_webhook.ps1
powershell -ExecutionPolicy Bypass -File scripts\payment_status.ps1
```

正常结果应显示本机 `8511` 正在监听，且 `/health` 返回 `200`。
不要在天翼云安全组中开放 8511。

## 5. 配置 HTTPS 反向代理

公网只开放 443。由 IIS、Cloudflare Tunnel 或其他 HTTPS 入口将：

```text
https://你的域名/webhooks/creem
```

反向代理到：

```text
http://127.0.0.1:8511/webhooks/creem
```

Creem Webhook 中填写公网 HTTPS 地址，并至少订阅：

```text
checkout.completed
subscription.active
subscription.paid
subscription.canceled
subscription.scheduled_cancel
subscription.past_due
subscription.expired
subscription.paused
refund.created
dispute.created
```

## 6. 测试验收

1. 使用测试会员登录，打开“订阅与续费”。
2. 创建测试结账并完成 Creem 测试付款。
3. 返回系统后点击“刷新付款结果”。
4. 确认会员到期时间延长，订单为“支付成功”。
5. 管理员确认回调状态为“已处理”，且没有金额或商品校验错误。
6. 在 Creem 重发同一个回调，确认会员时间不会再次延长。
7. 执行 `scripts\payment_status.ps1` 和 `scripts\release_check.py`。

## 7. 正式模式

只有在 Creem 账户审核通过、域名 HTTPS 可用、测试链路全部通过并完成备份后，才可：

```text
CREEM_MODE=live
CREEM_LIVE_API_KEY=正式API密钥
CREEM_LIVE_WEBHOOK_SECRET=正式回调密钥
CREEM_LIVE_ENABLED=true
```

正式与测试环境必须分别配置商品映射。首次正式付款应使用低金额、自有账号完成，
确认扣款、回调、会员续期、退款和订单审计全部正常后再扩大使用范围。
