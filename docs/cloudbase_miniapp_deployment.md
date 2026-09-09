# 微信云开发部署方案

## 目标与边界

本方案只给微信小程序增加云开发入口，不替换 PC 端：

- PC 端继续由 `app.py` 在 `8501` 端口运行。
- Windows 服务器、计划任务、发布脚本和原数据库路径保持原样。
- 小程序 API 作为独立容器部署到微信云托管，当前环境服务名为
  `gupiaoxiaochengxu`。
- 小程序可在“现有 HTTP 接口”和“微信云托管”之间切换；云端异常时关闭开关即可回退。

## 为什么采用云托管

当前后端是常驻 Python HTTP 服务，接口多、依赖行情库，而且会读写 SQLite。将它拆成大量云函数
会重复改造业务逻辑，也更容易和 PC 端产生差异。云托管可以直接运行现有 Python API 容器，
小程序通过 `wx.cloud.callContainer` 调用，改动集中在部署和身份适配层。

云托管会向服务注入 `X-WX-OPENID`、`X-WX-APPID` 等可信请求头。后端仅在
`MINIAPP_TRUST_CLOUDBASE_IDENTITY=true` 时使用这些请求头，并校验 AppID。不要在普通公网服务上
开启此开关。

## 数据持久化

云托管容器本地磁盘不是持久存储。本项目在云端继续使用经过验证的 SQLite 业务层，但会在容器启动
时从 CloudBase PostgreSQL 恢复完整快照，并在持仓、预警和模拟交易等写操作后同步回 PostgreSQL。
这样既不改动 PC 端存储，也无需额外购买 CFS。

仓库中的 `deploy/cloudbase/stock_recommendations.seed.gz` 只包含公开行情、推荐和复盘数据；构建脚本会
清空持仓、预警、模拟交易、交易账本和个人查询记录。首次启动或数据库暂不可用时用该种子提供只读
行情。个人数据永远不提交 Git。

SQLite 云端使用 `delete` 回滚日志模式，PC 端仍保持 WAL。当前快照同步方案要求云托管最大实例数为
1；最小实例数可设为 0，以便体验阶段按需启动并节省资源。将来需要多人高并发时，再把各业务表原生
迁移到 PostgreSQL 后开放多实例。

个人版可开启 `MINIAPP_CLOUDBASE_PERSONAL_MODE=true`，直接使用 CloudBase 网关注入并校验过的微信
OpenID，不依赖 PC 端 MySQL。准备售卖会员时应关闭个人模式，改用正式会员数据库、独立角色权限和
订阅校验。

## 一、创建云环境

1. 在微信开发者工具打开 AppID `wx7ded5b1d303e1b1d` 对应的小程序。
2. 点击“云开发”，创建与该小程序绑定的云环境，记录环境 ID。
3. 开通云托管，创建或更新服务 `gupiaoxiaochengxu`。
4. 构建上下文选择仓库根目录，Dockerfile 路径填写：

```text
deploy/cloudbase/Dockerfile
```

5. 服务端口填写 `80`，健康检查路径填写 `/health`。

体验阶段设置最小实例数为 0、最大实例数为 1；正式提供稳定服务后可把最小实例数调整为 1。

## 二、初始化 CloudBase PostgreSQL

在 CloudBase PostgreSQL 中创建一个仅供该云托管服务使用的数据库账号，并取得内网连接串。账号只需
对自己的 schema 拥有建表和读写权限。连接串作为云托管加密环境变量保存：

```text
MINIAPP_CLOUD_DATABASE_URL=postgresql://用户:密码@内网地址:端口/数据库?sslmode=require
```

不要把连接串写入 Git、Dockerfile、小程序源码或聊天记录。服务首次启动会自动创建
`stock_quant_miniapp_state` 表并写入脱敏种子。

## 三、配置云托管环境变量

以下变量在云托管控制台设置，不提交到仓库：

```text
APP_ENV=production
AUTH_ENABLED=false
MINIAPP_TOKEN_SECRET=至少32位高强度随机字符串
MINIAPP_TRUST_CLOUDBASE_IDENTITY=true
MINIAPP_CLOUDBASE_PERSONAL_MODE=true
MINIAPP_CLOUD_SQLITE_SYNC=true
MINIAPP_CLOUD_DATABASE_URL=CloudBase PostgreSQL内网连接串
WECHAT_MINIAPP_APP_ID=wx7ded5b1d303e1b1d
```

Dockerfile 已提供 `PORT=80`、临时目录和监听地址。云托管模式通过可信身份头获得 OpenID，
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
  service: 'gupiaoxiaochengxu'
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
- 微信登录首次和再次均稳定识别同一 OpenID。
- 不同微信用户无法看到彼此的持仓、预警和模拟交易。
- 今日推荐日期、K 线日期和数据状态一致，不把历史缓存标成实时行情。
- 重启实例和重新部署后，PostgreSQL 快照中的个人数据仍存在。
- 关闭公网入口后，小程序仍能通过 `callContainer` 正常访问。
- 云端异常时把对应环境的 `enabled` 改为 `false`，现有接口可立即接管。

## 官方参考

- [小程序调用云托管](https://docs.cloudbase.net/run/develop/access/mini)
- [已有应用迁移到云托管](https://docs.cloudbase.net/run/best-practice/migration)
- [Python 应用容器化](https://docs.cloudbase.net/run/quick-start/dockerize-python)
- [连接 CloudBase PostgreSQL](https://docs.cloudbase.net/database/postgresql/connecting-to-postgresql)
