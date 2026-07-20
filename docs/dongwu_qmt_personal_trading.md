# 东吴QMT个人实盘决策台

## 适用范围

该模块仅供系统管理员本人使用，普通会员不可见。它通过东吴证券官方迅投QMT的
XtQuant接口读取行情、资金、持仓和委托，并在人工确认后提交限价委托。

系统不保存证券账户交易密码。QMT客户端必须由账户本人在受控Windows机器上登录。

## 开通顺序

1. 联系东吴证券客户经理或拨打95330，申请“东吴证券迅投QMT交易系统”和Python量化接口权限。
2. 审核通过后安装东吴QMT/MiniQMT，确认人工登录、行情、委托和撤单均正常。
3. 找到QMT的`userdata_mini`完整目录。
4. 使用QMT随附的`xtquant`组件，确保运行本系统的Python能够执行`import xtquant`。
5. 先保持`QMT_LIVE_TRADING=false`，至少连续观察或模拟20个交易日。
6. 核对金额限制、价格偏离限制和账户权限后，再单独开启实盘开关。

## Windows环境变量

以管理员PowerShell执行，路径和账号替换为本人实际信息：

```powershell
[Environment]::SetEnvironmentVariable("ENABLE_PERSONAL_TRADING", "true", "Machine")
[Environment]::SetEnvironmentVariable("QMT_USERDATA_PATH", "D:\东吴QMT\userdata_mini", "Machine")
[Environment]::SetEnvironmentVariable("QMT_ACCOUNT_ID", "本人资金账号", "Machine")
[Environment]::SetEnvironmentVariable("QMT_LIVE_TRADING", "false", "Machine")
[Environment]::SetEnvironmentVariable("QMT_MAX_ORDER_NOTIONAL", "50000", "Machine")
[Environment]::SetEnvironmentVariable("QMT_MAX_PRICE_DEVIATION_PCT", "2", "Machine")
```

重启`StockQuantWeb`服务后，管理员左侧会出现“个人实盘决策台”。

## 推荐部署结构

- 对外售卖服务器：保持`ENABLE_PERSONAL_TRADING=false`，只提供选股、复盘、持仓方案和预警。
- 本人交易电脑：安装东吴QMT并开启个人决策台，交易账号和QMT目录只保存在本机系统环境变量中。
- 不建议把QMT资金账号、交易终端和个人实盘入口放在公开互联网服务器。

## 实盘启用

完成模拟验证后，才设置：

```powershell
[Environment]::SetEnvironmentVariable("QMT_LIVE_TRADING", "true", "Machine")
```

每笔真实委托仍需输入“确认实盘委托”；撤单需输入“确认撤单”。系统会检查：

- A股买入数量为100股整数倍；
- 单笔金额不超过`QMT_MAX_ORDER_NOTIONAL`；
- 委托价偏离最新价不超过`QMT_MAX_PRICE_DEVIATION_PCT`；
- 买入不超过可用资金；
- 卖出不超过可卖数量；
- 未取得实时价格、未同步账户或QMT断线时禁止提交。

## 重要说明

自动实盘不能消除数据延迟、涨跌停封单、停牌、网络中断、QMT断线、滑点和交易所拒单等风险。
任何策略都应先做模拟和小额验证，并保留人工停止开关。
