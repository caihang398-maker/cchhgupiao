# 微信小程序部署说明

采用微信云开发/云托管时，请同时阅读
[`docs/cloudbase_miniapp_deployment.md`](cloudbase_miniapp_deployment.md)。云托管是小程序的独立部署
入口，不会替换本文件描述的 PC 端和 Windows 服务器运行方式。

## 1. 架构边界

- PC 端保持不变：`app.py` 和 `dashboard.py` 继续由 Streamlit 在 `8501` 运行。
- 小程序前端独立：代码位于 `miniapp/`，使用微信原生 WXML、WXSS 和 JavaScript。
- 小程序 API 独立：`stock_quant.miniapp_api` 默认监听 `127.0.0.1:8512`。
- 数据保持一致：推荐、行情缓存、持仓和预警继续使用现有 SQLite；会员账号继续使用现有 MySQL。
- 扫描、管理员、导出、支付配置和实盘相关操作继续留在 PC 端，避免移动端误触高风险任务。

## 2. 当前小程序功能

- 微信快捷登录：首次进入自动创建独立体验账号，后续按 OpenID 识别同一用户；保留 PC 会员账号登录作为备用方式。
- 今日推荐、短中长期切换、数据日期和过期提醒。
- 首页今日行动清单：数据时效、待生成持仓方案、风险预警和重点候选统一排序。
- 股票名称/代码/行业/题材搜索。
- 手机组合条件选股：资金、技术、买点、估值、现金流、分红、主线和可信度。
- 日K线、5日线、20日线、买点、止损、目标位、仓位和投研解释。
- 推荐可信度：胜率、阶段收益、最大回撤、止损率、目标触达率。
- 市场宽度、市场温度、情绪周期、资金热点、主线方向和涨停梯队。
- 持仓录入、移出、做T比例和当日回本方案。
- 预警规则新增、启停、手工检查和最近触发记录。
- 推荐池、持仓和市场情绪一键预警，并自动跳过重复规则。
- 模拟交易账户：推荐池模拟买入、资金约束、胜率、收益和最大回撤。
- 数据体检中心：行情日期、覆盖率、失败原因、备用方案和决策可用状态。
- 推荐复盘、会员状态和风险说明。

## 2.1 AppID 确定后的移动端增强

- 微信订阅消息：只在用户主动授权后发送买点、止损和数据异常提醒。
- 真机网络诊断：展示域名、证书、接口延迟和最近一次成功连接时间。
- 自选关注分组：把“关注”与真实持仓分开，支持标签和观察理由。
- 盘中分时图：接入具备授权的实时行情源后再开放，不用日线缓存冒充实时数据。
- 个人券商联动：等东吴证券量化权限和正式接口确认后，先做只读查询与模拟下单，最后才评估实盘。

## 2.2 个人主体虚拟支付

微信现已向符合条件的个人主体工具类小程序开放虚拟支付。会员天数属于虚拟商品，计划按
月卡、季卡、半年卡、年卡四个道具接入。当前代码先完成资格检查、套餐展示和正式支付保护，
在 AppID、OfferID、现网 AppKey、道具 ID、发货推送和查单兜底全部完成前，不会创建真实
支付或扣款。

开通条件：

- 小程序主体为个人，开发者持有中国大陆居民身份证。
- 服务类目包含“工具”。股票分析类目的最终审核结果以微信后台为准，不通过错误类目规避审核。
- 小程序已完成认证和备案。
- 在微信公众平台“支付与交易 / 虚拟支付”完成申请、签约和道具发布。

微信快捷登录使用 `wx.login` 换取的 OpenID 识别账号。OpenID 不是微信号，系统不能直接读取
用户的微信号；`AppSecret`、`session_key` 和支付 AppKey 只能留在服务器端。

官方参考：

- [个人主体虚拟支付](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/business-capabilities/virtual-payment/person.html)
- [`wx.requestVirtualPayment`](https://developers.weixin.qq.com/miniprogram/dev/api/payment/wx.requestVirtualPayment.html)

## 3. 本机开发

先确保 PC 项目的虚拟环境已经安装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1
```

启动小程序 API：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_miniapp_local.ps1
```

健康检查：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8512/health
```

在微信开发者工具中选择“导入项目”，目录选择仓库内的 `miniapp`。申请中的 AppID
尚未取得时可先使用测试号；取得 AppID 后，将 `miniapp/project.config.json` 的
`appid` 改为真实 AppID。开发者工具本机调试时，可暂时勾选“不校验合法域名、web-view、
TLS版本以及HTTPS证书”。此开关不能代替体验版和正式版的域名配置。

本机开发默认请求：

```text
http://127.0.0.1:8512/api/v1
```

真机不能把 `127.0.0.1` 当作电脑地址。真机体验应直接连接已经部署好 HTTPS 的服务器。

## 4. 服务器数据库迁移

在现有 MySQL 会员库执行：

```powershell
mysql -u root -p < database\migrations\20260906_wechat_miniapp.sql
mysql -u root -p < database\migrations\20260907_miniapp_direct_login_subscription.sql
```

第一条迁移只新增 `miniapp_user_bindings`；第二条只补充“半年卡”套餐。两条迁移均不删除或
覆盖已有会员、订单和支付数据，并可重复执行。

## 5. 服务器环境变量

以下值应设置为 Windows“机器”环境变量，不能写进小程序代码或提交到 Git：

```text
APP_ENV=production
AUTH_ENABLED=true
MINIAPP_API_HOST=127.0.0.1
MINIAPP_API_PORT=8512
MINIAPP_TOKEN_SECRET=至少32位的高强度随机字符串
MINIAPP_TOKEN_DAYS=7
MINIAPP_TRUST_PROXY=true
MINIAPP_WECHAT_AUTO_REGISTER=true
MINIAPP_WECHAT_TRIAL_DAYS=7
WECHAT_MINIAPP_APP_ID=你的小程序AppID
WECHAT_MINIAPP_APP_SECRET=你的小程序AppSecret
WECHAT_MINIAPP_SUBJECT_TYPE=individual
WECHAT_MINIAPP_CATEGORY_TOOLS=false
WECHAT_MINIAPP_VERIFIED=false
WECHAT_MINIAPP_ICP_FILED=false
MINIAPP_PAYMENT_MODE=disabled
MINIAPP_PAYMENT_LIVE_ENABLED=false
WECHAT_VIRTUAL_PAY_OFFER_ID=
WECHAT_VIRTUAL_PAY_APP_KEY=
```

生成随机令牌密钥的 PowerShell 示例：

```powershell
$bytes = New-Object byte[] 48
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[Convert]::ToBase64String($bytes)
```

`AppSecret` 只保存在服务器。小程序前端只包含 AppID，不得包含 AppSecret、数据库密码、
Creem 密钥或券商密钥。

取得真实资料后，按实际状态把三个资格开关改为 `true`，并将
`MINIAPP_PAYMENT_MODE` 改为 `wechat_virtual`。即使支付参数齐全，
`MINIAPP_PAYMENT_LIVE_ENABLED` 也应保持 `false`，直到下单签名、发货推送幂等、主动查单、
退款处理和小额真单全部验收通过。

## 6. 安装 API 计划任务

使用管理员 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_miniapp_api.ps1
```

检查状态：

```powershell
Get-ScheduledTask -TaskName StockQuantMiniappApi
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8512/health
Get-Content E:\stock-quant\logs\miniapp-api.log -Tail 100
```

API 默认只监听本机，由 IIS 或 Nginx 提供公网 HTTPS，不需要在云安全组开放 `8512`。

## 7. HTTPS 反向代理

小程序生产配置默认请求：

```text
https://ch.cchhgp.sbs/api/miniapp/v1
```

如果更换域名，请同步修改 `miniapp/config.js` 中 `trial` 和 `release` 的地址。

IIS URL Rewrite 可新增一条规则：

```text
匹配：^api/miniapp/(.*)
转发：http://127.0.0.1:8512/api/{R:1}
```

代理必须保留请求方法、请求正文和 `Authorization` 请求头。外部验证示例：

```powershell
Invoke-WebRequest -UseBasicParsing https://ch.cchhgp.sbs/api/miniapp/v1/health
```

## 8. 微信后台配置

在小程序后台的“开发管理/开发设置/服务器域名”中，把 request 合法域名设置为：

```text
https://ch.cchhgp.sbs
```

只填写域名，不填写 `/api/miniapp/v1` 路径。体验版和正式版要求域名、证书、TLS、备案和
平台类目都满足微信当前审核规则。股票分析、投资信息等内容可能涉及受限类目或资质；
提交审核前应按小程序后台当时展示的类目和材料要求核验。个人自用阶段可先用开发版或
体验版测试，不应通过错误类目规避审核。

## 9. 上传发布

1. 在微信开发者工具中使用真实 AppID 打开 `miniapp`。
2. 关闭“不校验合法域名”后完成登录、推荐、K线、持仓、预警和复盘真机测试。
3. 点击“上传”，填写版本号和说明。
4. 在小程序后台选择该构建为体验版。
5. 个人自用可先停留在体验版；需要正式发布时再提交审核并确认类目、隐私和备案条件。

## 10. 发布前检查

- PC 端 `8501` 正常，且小程序 API `8512` 健康检查返回 200。
- 推荐数据日期是当日或最近交易日，不把过期缓存当作实时数据。
- HTTPS 域名证书链完整，且小程序后台已配置合法域名。
- 微信快捷登录首次创建账号后，退出再登录仍能识别同一会员。
- 首次微信登录只创建一个体验账号，并且不会把 OpenID、session_key 或支付密钥返回到前端。
- 会员到期后仍可登录并查看续费页，但推荐、持仓和预警接口返回会员到期状态。
- 月卡、季卡、半年卡、年卡均显示，未通过支付验收时只能查看开通说明。
- 不同会员只能看到自己的持仓和预警。
- 日K接口失败时显示明确错误或缓存提示，不伪装成实时行情。
- 页面没有“必涨、稳赚、保本”等承诺性文字。
- AppSecret、数据库密码、支付密钥和券商密钥均未进入小程序包或 Git。
