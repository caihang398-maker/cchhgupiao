# 微信云开发部署方案

## 目标与边界

本方案只给微信小程序增加云开发入口，不替换 PC 端：

- PC 端继续由 `app.py` 在 `8501` 端口运行。
- Windows 服务器、计划任务、发布脚本和原数据库路径保持原样。
- 小程序 API 作为独立容器部署到微信云托管，服务名固定为
  `stock-quant-miniapp-api`。
- 小程序可在“现有 HTTP 接口”和“微信云托管”之间切换；云端异常时关闭开关即可回退。

## 为什么采用云托管

当前后端是常驻 Python HTTP 服务，接口多、依赖行情库，而且会读写 SQLite。将它拆成大量云函数
会重复改造业务逻辑，也更容易和 PC 端产生差异。云托管可以直接运行现有 Python API 容器，
小程序通过 `wx.cloud.callContainer` 调用，改动集中在部署和身份适配层。

云托管会向服务注入 `X-WX-OPENID`、`X-WX-APPID` 等可信请求头。后端仅在
`MINIAPP_TRUST_CLOUDBASE_IDENTITY=true` 时使用这些请求头，并校验 AppID。不要在普通公网服务上
开启此开关。

## 数据持久化

云托管容器本地磁盘不是持久存储。必须在首次上线前创建 CFS 文件存储，并挂载到：

```text
/mnt/stock-quant-data
```

容器通过 `STOCK_QUANT_DATA_DIR` 把 SQLite、缓存放到该目录。未挂载 CFS 时虽然服务能够启动，
但扩缩容或重新部署后数据可能丢失，因此不得作为正式环境使用。镜像已开启
`MINIAPP_REQUIRE_PERSISTENT_STORAGE=true`，没有检测到挂载点或目录不可写时会主动拒绝启动，避免
误把临时盘当成正式数据库。

SQLite 的 WAL 模式不适合网络文件系统。云容器已单独使用 `delete` 回滚日志模式，PC 端仍保持
原来的 WAL 模式。云托管服务必须把最小实例和最大实例都设为 1，不能让多个容器同时写同一个
SQLite 文件。这是个人使用和少量测试用户阶段的过渡方案；需要扩容前应把云端持仓、预警、模拟
交易和推荐数据迁移到 MySQL，再开放多实例。

会员账号仍使用现有 MySQL。MySQL 必须允许云托管所在网络访问，并限制来源、使用专用低权限账号。
不应把数据库密码写入 Git、小程序代码或 Dockerfile。

推荐数据的每日刷新是独立任务：第一阶段继续保留现有 PC/Windows 扫描任务；迁移云端刷新任务前，
需要把最新 SQLite 安全同步到 CFS。第二阶段再建立云端定时扫描作业，且同一时刻只允许一个写入者，
避免 SQLite 并发写入冲突。

## 一、创建云环境

1. 在微信开发者工具打开 AppID `wx7ded5b1d303e1b1d` 对应的小程序。
2. 点击“云开发”，创建与该小程序绑定的云环境，记录环境 ID。
3. 开通云托管，创建服务 `stock-quant-miniapp-api`。
4. 构建上下文选择仓库根目录，Dockerfile 路径填写：

```text
deploy/cloudbase/Dockerfile
```

5. 服务端口填写 `80`，健康检查路径填写 `/health`。

当前 SQLite 过渡方案必须固定为 1 个实例，不允许自动扩容到多个实例。为了避免冷启动和重复写入，
建议最小实例与最大实例都设置为 1。

## 二、挂载 CFS

在云托管服务的“文件存储/存储挂载”中创建或选择 CFS，并把挂载路径设置为：

```text
/mnt/stock-quant-data
```

首次切流量前确认容器对该目录具有读写权限，并完成当前
`data/stock_recommendations.sqlite3` 的初始化或同步。数据库文件、`-wal` 和 `-shm` 文件必须位于
同一挂载目录。

## 三、配置云托管环境变量

以下变量在云托管控制台设置，不提交到仓库：

```text
APP_ENV=production
AUTH_ENABLED=true
MINIAPP_TOKEN_SECRET=至少32位高强度随机字符串
MINIAPP_WECHAT_AUTO_REGISTER=true
MINIAPP_WECHAT_TRIAL_DAYS=7
MINIAPP_TRUST_CLOUDBASE_IDENTITY=true
WECHAT_MINIAPP_APP_ID=wx7ded5b1d303e1b1d
DB_HOST=你的MySQL地址
DB_PORT=3306
DB_NAME=stock_quant_saas
DB_USER=小程序专用数据库账号
DB_PASSWORD=数据库强密码
```

Dockerfile 已提供 `PORT=80`、持久化目录和监听地址。云托管模式通过可信身份头获得 OpenID，
不需要配置 `WECHAT_MINIAPP_APP_SECRET`。如仍保留现有服务器的 `wx.login` 换码模式，AppSecret 只放在
现有服务器环境变量中。

## 四、关闭不必要的公网入口

完成小程序调用测试后，在云托管控制台关闭服务公网访问，只保留微信云调用。这样外部请求无法
自行伪造 `X-WX-OPENID`。如因调试暂时开启公网访问，不得同时把生产会员数据和支付能力开放出去。

## 五、切换小程序请求方式

编辑 `miniapp/cloud.config.js`，只在准备测试的环境填入云环境 ID。例如先切开发版：

```javascript
develop: {
  enabled: true,
  envId: '你的云环境ID',
  service: 'stock-quant-miniapp-api'
}
```

建议按以下顺序切换：

1. `develop`：开发者工具和真机调试。
2. `trial`：体验版验收。
3. `release`：正式版审核通过后启用。

没有填写环境 ID或 `enabled=false` 时，会自动继续使用 `miniapp/config.js` 中原有 HTTP 地址。

## 六、验收清单

- PC 端 `http://127.0.0.1:8501` 可正常登录和使用。
- 本地小程序 API `http://127.0.0.1:8512/health` 仍返回 200。
- 云托管 `/health` 返回 200，且显示微信登录已配置。
- 微信登录首次只创建一个会员，再次登录仍识别同一 OpenID。
- 不同微信用户无法看到彼此的持仓、预警和模拟交易。
- 今日推荐日期、K 线日期和数据状态一致，不把历史缓存标成实时行情。
- 重启实例、重新部署和扩缩容后，CFS 中的数据仍存在。
- 关闭公网入口后，小程序仍能通过 `callContainer` 正常访问。
- 云端异常时把对应环境的 `enabled` 改为 `false`，现有接口可立即接管。

## 官方参考

- [小程序调用云托管](https://docs.cloudbase.net/run/develop/access/mini)
- [已有应用迁移到云托管](https://docs.cloudbase.net/run/best-practice/migration)
- [Python 应用容器化](https://docs.cloudbase.net/run/quick-start/dockerize-python)
- [云托管挂载 CFS](https://docs.cloudbase.net/en/run/deploy/configuring/storage/cfs)
