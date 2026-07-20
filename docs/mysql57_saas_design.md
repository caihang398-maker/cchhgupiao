# MySQL 5.7 SaaS 数据库设计

## 技术现状

系统使用 Python 开发：

- Web 界面：Streamlit
- 量化和数据处理：Pandas、NumPy、AkShare
- 图表：Plotly
- 公共股票数据：SQLite，保存行情缓存、扫描批次、推荐和复盘结果
- 会员业务数据：MySQL，保存账号、权限、服务期、订单、审计和逐日查询记录
- 服务器目标：Windows Server 2016 + MySQL 5.7，并保留本地 SQLite 公共数据文件

数据库脚本位于 `database/mysql57_schema.sql`。脚本按 MySQL 5.7.9 及以上版本设计，字符集为 `utf8mb4`，存储引擎为 InnoDB。
应用账号的最小权限示例位于 `database/mysql57_app_user.sql.example`。

当前发布版本采用混合存储：应用已经实际读写 SQLite 中的公共股票数据，并实际读写 MySQL
中的会员和审计数据。MySQL 脚本同时预留了公共股票数据表，便于后续多实例部署时迁移；
在完成对应的数据访问层迁移前，不应把这些预留表理解为当前程序已在使用。

应用层已提供可配置启用的登录和管理员账号页面。数据库初始化完成后设置：

```powershell
$env:AUTH_ENABLED="true"
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_NAME="stock_quant_saas"
$env:DB_USER="stock_quant_app"
$env:DB_PASSWORD="数据库应用账号密码"
streamlit run app.py
```

未设置 `AUTH_ENABLED=true` 时保持本地开发模式，不要求登录。

## 数据分层

### 账号与权限

- `sys_users`：手机号、姓名、密码哈希、账号状态、开通及到期时间。
- `sys_roles`：管理员、会员等角色。
- `sys_permissions`：具体功能权限。
- `sys_user_roles`：账号与角色关系。
- `sys_role_permissions`：角色与权限关系。
- `auth_sessions`：登录会话，只保存令牌哈希。
- `login_logs`：成功和失败登录记录。
- `audit_logs`：管理员开通、延期、禁用、退款等操作记录。

普通会员默认拥有全部股票查询功能，但没有账号、订单和系统管理权限。管理员拥有全部权限。

### 订阅与支付

- `subscription_plans`：月卡、季卡、年卡及价格、每日查询上限。
- `subscription_orders`：购买订单和服务期快照。
- `payment_transactions`：支付、退款流水。
- `subscription_periods`：每一次开通、续费、赠送或手工调整的服务周期。

`sys_users.service_expires_at` 是登录鉴权使用的快速缓存；`subscription_periods` 是订阅历史依据。延期时两处必须在同一事务中更新。

服务期采用左闭右开规则：

```text
service_started_at <= 当前UTC时间 < service_expires_at
```

例如 2026-06-08 购买一个月，到期时间是 2026-07-08 的同一时刻，不使用“当月30天”的近似算法。

### 公共股票数据

当前程序把以下公共数据保存在项目 `data` 目录的 SQLite 数据库中，所有会员共享一份。
MySQL 脚本中的同名或对应表为后续集中化部署预留：

- `scan_runs`：每日全市场扫描批次。
- `recommendations`：短期、中期、长期推荐及资金、估值、现金流、分红因子。
- `market_snapshots`：市场上涨、下跌、成交额和全球风险快照。
- `capital_hotspots`：每日、每周、每月行业和题材资金热点。

这些数据对所有会员相同，只保存一份。用户查看行为通过查询记录关联，不为每个会员复制整批市场数据。

### 每个账号每日数据

- `user_query_records`：每次查询一行，保存用户、日期、查询类型、股票、请求条件和用户当时看到的结果快照。
- `user_stock_analysis_results`：个股分析的结构化买点、卖点、止损、目标位和评分，便于按用户、股票、日期统计。
- `user_daily_usage`：每个用户每天一行，汇总登录、查询、个股分析、推荐查看、热点查看和导出次数。

常用查询：

```sql
-- 查询某个账号某天的全部操作结果
SELECT *
FROM user_query_records
WHERE user_id = ?
  AND query_date = ?
ORDER BY created_at;

-- 查询某个会员对一只股票的历史买卖点
SELECT *
FROM user_stock_analysis_results
WHERE user_id = ?
  AND symbol = ?
ORDER BY trade_date DESC, created_at DESC;

-- 查询未来7天内到期的账号
SELECT *
FROM v_user_account_status
WHERE service_status = 'valid'
  AND service_expires_at < DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY service_expires_at;

-- 查询已到期账号
SELECT *
FROM v_user_account_status
WHERE service_status = 'expired'
ORDER BY service_expires_at;

-- 查询某天使用量最高的会员
SELECT u.mobile, u.real_name, d.*
FROM user_daily_usage d
JOIN sys_users u ON u.id = d.user_id
WHERE d.usage_date = ?
ORDER BY d.query_count DESC;
```

## 续费事务

支付成功或管理员手工续费时：

1. 使用 `SELECT ... FOR UPDATE` 锁定账号。
2. 新周期开始时间取 `GREATEST(UTC_TIMESTAMP(), service_expires_at)`。
3. 使用 `DATE_ADD(start_at, INTERVAL duration_months MONTH)` 计算结束时间。
4. 写入 `subscription_periods`。
5. 更新订单状态和 `sys_users.service_expires_at`。
6. 写入 `audit_logs`。
7. 提交事务。

这样连续购买多个月不会覆盖旧记录，也不会因为并发支付丢失服务时间。

## 管理员初始化

先执行数据库脚本，再安装服务器依赖：

```powershell
mysql -uroot -p < database/mysql57_schema.sql
pip install -r requirements-server.txt
```

然后把 `database/mysql57_app_user.sql.example` 复制为服务器私有脚本，替换随机密码和允许访问的主机后，由 MySQL 管理员执行。不要把真实密码提交到代码库。

通过环境变量创建管理员，密码不会写入命令历史：

```powershell
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_NAME="stock_quant_saas"
$env:DB_USER="stock_quant_app"
$env:DB_PASSWORD="数据库应用账号密码"
$env:ADMIN_MOBILE="13800000000"
$env:ADMIN_REAL_NAME="系统管理员"
$env:ADMIN_LOGIN_NAME="admin"
python scripts/create_admin.py
```

脚本会交互式要求输入管理员登录密码，并使用 bcrypt 保存哈希。不得把明文密码或会话令牌写入数据库。

## 部署建议

- MySQL 不直接暴露到公网，只允许应用服务器访问 3306。
- 应用使用单独的 MySQL 账号，不使用 `root`。
- Web 入口必须启用 HTTPS，可由 IIS 反向代理到 Streamlit。
- Streamlit 进程注册为 Windows 服务，并设置自动重启。
- 每日执行数据库备份，并定期做恢复演练。
- 所有数据库时间保存为 UTC，界面统一转换为 Asia/Shanghai。
- 管理员修改账号、服务期、订单和权限时必须写入 `audit_logs`。
- 查询明细量大后按月归档旧数据。MySQL 5.7 的 InnoDB 分区表不能参与外键关系，因此初期不对这些业务表直接做分区。

## 版本提醒

MySQL 5.7 自 2023-10-25 起已进入 Sustaining Support。脚本兼容 5.7，
但新服务器优先选择仍受支持的 MySQL 版本；如果现有环境必须使用 5.7，
至少使用 5.7.44，并限制网络访问、做好备份。

Windows Server 2016 的扩展支持将在 2027-01-12 结束。若软件准备长期按月销售，
建议直接评估 Windows Server 2022 或更新版本，避免上线后很快再次迁移操作系统。
