# Windows Server 发布指南

## 发布架构

- Python 3.12 + Streamlit：网页和量化计算。
- MySQL 5.7：会员、权限、服务期、登录日志、审计日志和用户查询记录。
- SQLite：公共行情缓存、每日推荐、推荐复盘和个股分析缓存。
- IIS：HTTPS 入口和反向代理，Streamlit 仅监听服务器内部端口。

账号数据与公共股票数据分开保存，避免为每个会员复制整套市场数据。SQLite 已启用外键、10秒锁等待和 WAL 并发模式，数据库路径固定为项目目录下的 `data` 文件夹，不受服务启动目录影响。

## 首次部署

推荐把发布压缩包解压到 `E:\stock-quant`，以管理员身份打开 PowerShell。

```powershell
cd E:\stock-quant
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-server.txt
```

使用 MySQL 管理员执行：

```powershell
mysql -uroot -p < database/mysql57_schema.sql
```

根据 `database/mysql57_app_user.sql.example` 创建最小权限应用账号，然后设置系统环境变量：

```powershell
$env:APP_ENV="production"
$env:AUTH_ENABLED="true"
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_NAME="stock_quant_saas"
$env:DB_USER="stock_quant_app"
$env:DB_PASSWORD="高强度随机密码"
```

也可以在数据库结构和应用账号准备完成后运行自动安装脚本。脚本会交互输入数据库密码，
安装依赖、执行发布检查、注册开机自启任务并检查服务健康状态：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_server.ps1 `
  -WebPort 8501 `
  -BindAddress 127.0.0.1 `
  -DbHost 127.0.0.1 `
  -DbPort 3306 `
  -DbName stock_quant_saas `
  -DbUser stock_quant_app
```

默认只允许服务器本机访问 8501，适合由 IIS 反向代理提供 HTTPS。临时直接测试时可以将
`BindAddress` 改为 `0.0.0.0` 并增加 `-OpenFirewall`，但正式发布不建议绕过 HTTPS。

创建管理员：

```powershell
$env:ADMIN_MOBILE="13800000000"
$env:ADMIN_REAL_NAME="系统管理员"
$env:ADMIN_LOGIN_NAME="admin"
python scripts/create_admin.py
```

## 发布前检查

```powershell
python -m unittest discover -s tests -v
python scripts/release_check.py
python scripts/backup_data.py
```

开发电脑应使用统一构建脚本生成发布包。脚本会先编译并运行单元测试，再生成
`release_manifest.json`，记录策略版本和每个文件的 SHA-256，防止压缩包漏文件或服务器仍运行旧模块：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

需要连同 Streamlit 页面初始化一起检查时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 -FullAppCheck
```

正式收费发布还必须完成两项独立检查，它们不能由技术自检或页面免责声明替代：

1. 由熟悉证券业务的专业人士确认软件提供选股、买卖时机和收费服务所需的许可或合作资质。
2. 取得行情、财务、资金流、资讯等数据在商业展示、缓存、再分发和收费场景下的授权。

在这两项完成前，只应把系统用于内部研究、演示和测试。

全部通过后启动：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -Port 8501
```

健康检查地址：

```text
http://127.0.0.1:8501/_stcore/health
```

查看任务、监听端口、健康状态和最近日志：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/server_status.ps1
```

## 计划任务

建议在 Windows 任务计划程序中使用虚拟环境里的 Python，并把“起始于”设置为项目根目录。

交易日收盘后生成推荐：

```powershell
.\.venv\Scripts\python.exe scripts\daily_push.py --no-send
```

每天夜间备份公共数据：

```powershell
.\.venv\Scripts\python.exe scripts\backup_data.py --retention-days 30
```

MySQL 账号和审计数据应另行使用 `mysqldump --single-transaction` 每日备份，并定期验证恢复流程。

## IIS 与安全

- 公网只开放 HTTPS，不直接开放 8501 和 3306。
- IIS 将 HTTPS 请求反向代理到 `127.0.0.1:8501`。
- MySQL 只允许应用服务器访问，应用不得使用 `root`。
- 正式环境必须设置 `AUTH_ENABLED=true`。
- 推送功能只接受企业微信、钉钉、飞书官方 HTTPS 域名；自建网关需通过 `PUSH_ALLOWED_HOSTS` 明确加入白名单。
- 数据库密码、机器人地址和管理员密码不得写入代码、脚本或 Git。
- 发布前确认行情、财务和资讯数据的商业使用授权，并对付费股票推荐、宣传文案和用户协议进行专业合规审查。

## 更新流程

1. 在开发电脑运行 `scripts\build_release.ps1`，只使用新生成且带清单的 ZIP。
2. 先执行 SQLite 与 MySQL 备份，再把 ZIP 解压到独立更新目录。
3. 在更新目录运行下方命令；脚本会验证压缩包和目标文件哈希、备份旧代码、执行发布检查并做健康检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_update.ps1 `
  -TargetRoot E:\stock-quant `
  -TaskName StockQuantWeb `
  -WebPort 8501
```

4. 浏览推荐、个股分析、推荐复盘、登录和管理员后台，确认页面策略版本和发布清单一致。
5. 切换 IIS 反向代理后观察日志和健康检查。
6. 出现异常时，更新脚本会自动恢复旧文件；数据库仍需按独立备份方案恢复。
