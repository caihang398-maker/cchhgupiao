# A股每日量化推荐

本项目是一个本地运行的 A 股量化推荐工作台，重点是每日按热点、题材、K线走势和成交量筛选高概率候选，并给出短期、中期、长期三类推荐。

## 运行

推荐使用 Python 3.12。在 Windows PowerShell 中可以一键创建独立环境、安装依赖、
运行测试并启动本地系统：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1 -Start
```

也可以手工安装：

```powershell
pip install -r requirements.txt
streamlit run app.py
```

当前默认地址：

```text
http://localhost:8501
```

## 微信小程序

仓库中的 [`miniapp/`](miniapp) 是独立的原生微信小程序前端，PC 端仍保持原来的
Streamlit 页面和 `8501` 端口。小程序通过独立的 `8512` API 读取同一套推荐、持仓、
预警和会员数据。

本机启动小程序 API：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_miniapp_local.ps1
```

然后使用微信开发者工具导入 `miniapp` 目录。首次开发可使用测试号，并在开发者工具中
临时关闭合法域名校验；体验版和正式版必须使用已配置的 HTTPS 合法域名。

完整的 AppID、微信快捷登录、个人主体虚拟支付准备、MySQL 迁移、HTTPS 反向代理和上传发布步骤见
[`docs/wechat_miniapp_setup.md`](docs/wechat_miniapp_setup.md)。

使用微信云开发/云托管部署小程序 API 时，使用独立 Docker 入口和双传输开关，具体见
[`docs/cloudbase_miniapp_deployment.md`](docs/cloudbase_miniapp_deployment.md)。该方案不会替换 PC 端
`8501` 服务；未启用云环境时，小程序继续使用现有接口。

## 从 GitHub 安装

```powershell
git clone https://github.com/caihang398-maker/cchhgupiao.git
cd cchhgupiao
powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1 -Start
```

更新已有安装：

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements-server.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File scripts\start_local_8501.ps1
```

仓库不会包含本地数据库、缓存、日志、会员资料或密码。生产环境配置请使用操作系统
环境变量，变量名称可参考 [`.env.example`](.env.example)，不要把真实值提交到 Git。

## 主要功能

- 全市场初筛：覆盖全部 A 股的 3 日、5 日、10 日主力净流入、同行业市盈率/市净率、经营现金流和连续年度现金分红。
- 每日推荐：点击“刷新并生成今日推荐”后，对基本面与资金面高分候选继续复核热点、K线和成交量。
- 周期分类：推荐结果分成短期、中期、长期，每类单独给出评分、买点、止损、目标位和卖出条件。
- 推荐池：最多30只唯一股票，每页显示5只，支持翻页及代码、名称、行业、题材、推荐原因搜索。
- 推荐原因：逐只展示趋势、均线、动量指标、相对强弱指标、突破、成交量、波动率和全球市场风险偏好。
- 市场宽度：展示全市场股票数、上涨/下跌/平盘、涨跌停估算、成交额、市场温度和上涨占比。
- 全球环境：参考道琼斯、纳斯达克、标普500、恒生等指数，作为低权重风险因子。
- 行业题材：重点推荐股票会补充题材关键词，能按行业或题材辅助判断。
- 资金热点：分别查看每日、每周、每月的主力资金热门行业和题材。
- 主线与龙头：综合日、周、月资金强度识别板块生命周期，并生成行业/题材龙一、龙二、龙三相对强弱曲线。
- 情绪周期：统计首板、二板、三板、连板高度、封板率、炸板率、跌停压力和昨日涨停反馈，识别冰点、修复、升温、高潮、分化与退潮。
- 行业筛选：支持电力、机器人、半导体、人工智能、新能源、医药、消费、金融、军工等大类。
- 当天买卖点：输入股票名称或代码，系统会把当天实时价拼入K线，分析当天买点、卖点、止损和目标。
- 持仓与做T规划：输入成本价、持股数量、可卖底仓、账户资产和可动用资金，结合实时价、波动率、支撑阻力、历史振幅、流动性、市场温度、资金热点及最新政策/国际事件标题，判断本轮是否适合做T，并给出执行顺序、三档分批计划、失效位和成本改善情景。
- 历史策略验证：个股页面可运行下一交易日成交、整手买卖、佣金、印花税和滑点约束下的回测。
- 推荐自动复盘：按每日最终推荐持续计算推荐后1日、5日、20日收益、目标/止损触发和最大回撤。
- 策略版本：每次扫描保存策略版本和筛选参数，避免不同规则的历史业绩混在一起。
- 名词与方法：独立页面解释筛选条件、技术指标、评分结构、数据口径和回测边界。
- 数据存储：推荐结果按天写入本地 SQLite 数据库。
- 会员权限：可配置启用 MySQL 登录，未登录无法访问业务页面；账号按月开通并校验到期时间。
- 管理员后台：管理员可创建、续费、停用和恢复会员，并查看有效、即将到期和已到期账号。
- 在线订阅：支持 Creem 测试结账、签名回调、幂等续期、订单记录和管理员支付审计；正式收款默认关闭。
- 微信小程序：独立移动端提供微信免密码登录、月/季/半年/年订阅展示、今日行动清单、条件选股、股票搜索、日K线、可信度、市场地图、持仓做T方案、一键预警、模拟交易、数据体检和复盘；与PC端共享业务数据。
- SaaS 数据库设计：已提供 MySQL 5.7 账号权限、订阅到期、订单支付、审计和用户逐日查询记录结构。

做T规划只用于情景推演。A股当天新买入股票不能当天卖出，区间会随行情变化；系统不会保证成交、盈利或回本。

## 数据表

数据库文件：

```text
data/stock_recommendations.sqlite3
```

核心表：

- `scan_runs`：每次刷新扫描的记录。
- `recommendations`：每日推荐股票，包含周期、评分、买点、止损、目标、行业、题材和卖出条件。
- `recommendation_outcomes`：每日推荐后1日、5日、20日收益、目标/止损命中和最大回撤。
- `capital_hotspots`：每轮扫描对应的日、周、月行业与题材资金流排名。
- `sentiment_snapshots`：每日涨停梯队、炸板率、昨日溢价和市场情绪评分。
- `limit_up_ladder`：每日涨停股票的连板层级、封板资金、封板时间和炸板次数。
- `stock_analysis`：单只股票输入分析记录。

Windows Server 2016 + MySQL 5.7 的 SaaS 数据库方案见
[`docs/mysql57_saas_design.md`](docs/mysql57_saas_design.md)，建库脚本见
[`database/mysql57_schema.sql`](database/mysql57_schema.sql)。

产品对标、现状走查及后续优先级见
[`docs/product_review.md`](docs/product_review.md)。

Windows Server 正式发布、备份、计划任务和 IIS 安全配置见
[`docs/release_guide.md`](docs/release_guide.md)。

Creem 测试订阅、回调服务、MySQL 迁移和正式模式启用步骤见
[`docs/creem_subscription_setup.md`](docs/creem_subscription_setup.md)。

发布前建议执行：

```powershell
python -m unittest discover -s tests -v
python scripts/release_check.py
python scripts/backup_data.py
```

## 说明

系统会尽量筛出“高概率上涨候选”，但股票市场不存在保证上涨的信号。推荐结果必须和止损、仓位、公告、流动性、市场环境一起使用，不构成收益承诺。

本项目仅用于量化研究、策略验证和决策辅助，不构成证券投资咨询、收益承诺或自动
交易授权。使用者需要自行确认行情数据许可、证券业务合规要求以及策略风险。默认
配置不会连接券商执行真实订单。

公开仓库不应包含个人实盘券商适配器、生产支付密钥、服务器账号或专有策略参数；
这些内容建议保存在独立私有仓库或生产环境中。

## 开源许可

本项目采用 [GNU Affero General Public License v3.0](LICENSE) 开源。修改后通过网络
向用户提供服务时，也需要按照该许可证提供对应源代码。行情数据、第三方接口和依赖
软件仍分别受其自身许可条款约束。
