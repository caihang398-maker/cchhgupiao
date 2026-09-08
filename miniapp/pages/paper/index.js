const { request } = require('../../utils/request')
const { price, percent, compactNumber, dateText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    generating: false,
    error: '',
    initialCash: 100000,
    cashPerTrade: 10000,
    maxCount: 5,
    account: {},
    metrics: [],
    trades: []
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadAccount()
  },

  onPullDownRefresh() {
    this.loadAccount().finally(() => wx.stopPullDownRefresh())
  },

  handleAmount(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [field]: event.detail.value })
  },

  changeCount(event) {
    this.setData({ maxCount: Number(event.detail.value) })
  },

  decorate(data) {
    const account = data.account || {}
    const summary = data.summary || {}
    const trades = (data.trades || []).map((item) => Object.assign({}, item, {
      date_text: dateText(item.trade_date),
      price_text: price(item.price),
      amount_text: compactNumber(item.amount),
      return_text: item.return_pct === null || item.return_pct === undefined
        ? '待复盘'
        : percent(item.return_pct, true),
      return_class: Number(item.return_pct) > 0 ? 'up' : Number(item.return_pct) < 0 ? 'down' : ''
    }))
    return {
      account,
      initialCash: account.initial_cash || this.data.initialCash,
      metrics: [
        { label: '账户权益', value: `¥${compactNumber(account.equity)}` },
        { label: '可用资金', value: `¥${compactNumber(account.cash)}` },
        { label: '累计收益', value: percent(summary.total_return, true) },
        { label: '历史胜率', value: percent(summary.win_rate) },
        { label: '最大回撤', value: percent(summary.max_drawdown, true), risk: true },
        { label: '模拟笔数', value: `${summary.trade_count || 0}笔` }
      ],
      trades
    }
  },

  loadAccount() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/paper' })
      .then((data) => this.setData(Object.assign(this.decorate(data), { loading: false })))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  generate() {
    if (this.data.generating) return
    this.setData({ generating: true, error: '' })
    request({
      url: '/paper/generate',
      method: 'POST',
      data: {
        initial_cash: Number(this.data.initialCash),
        cash_per_trade: Number(this.data.cashPerTrade),
        max_count: Number(this.data.maxCount)
      }
    })
      .then((data) => {
        this.setData(this.decorate(data))
        wx.showToast({ title: data.message || '模拟完成', icon: 'none' })
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ generating: false }))
  },

  openStock(event) {
    const symbol = event.currentTarget.dataset.symbol
    wx.navigateTo({ url: `/pages/stock/detail?symbol=${encodeURIComponent(symbol)}` })
  }
})
