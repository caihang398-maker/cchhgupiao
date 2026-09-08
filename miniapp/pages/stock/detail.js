const { request } = require('../../utils/request')
const { price, percent, compactNumber } = require('../../utils/format')

Page({
  data: {
    symbol: '',
    loading: true,
    error: '',
    detail: null,
    reportMetrics: [],
    credibilityMetrics: [],
    researchSections: [],
    sellTriggersText: '-',
    selectedDays: 120
  },

  onLoad(options) {
    this.setData({ symbol: decodeURIComponent(options.symbol || '') })
    this.loadDetail()
  },

  selectDays(event) {
    this.setData({ selectedDays: Number(event.currentTarget.dataset.days) })
    this.loadDetail()
  },

  loadDetail() {
    this.setData({ loading: true, error: '' })
    const symbol = encodeURIComponent(this.data.symbol)
    return request({ url: `/stocks/${symbol}?days=${this.data.selectedDays}`, timeout: 60000 })
      .then((detail) => {
        const report = detail.report || {}
        const recommendation = detail.recommendation || {}
        const credibility = recommendation.credibility || {}
        wx.setNavigationBarTitle({ title: detail.name || '股票详情' })
        this.setData({
          detail,
          reportMetrics: [
            { label: '当前价', value: price(report.close) },
            { label: '技术评分', value: `${report.score || 0}分` },
            { label: '买点区间', value: `${price(report.buy_zone_low)}-${price(report.buy_zone_high)}` },
            { label: '止损价', value: price(report.stop_loss), risk: true },
            { label: '目标一', value: price(report.take_profit_1) },
            { label: '建议仓位', value: percent(report.position_pct) }
          ],
          credibilityMetrics: [
            { label: '历史胜率', value: percent(credibility.win_rate) },
            { label: 'T+5收益', value: percent(credibility.t5_return, true) },
            { label: '最大回撤', value: percent(credibility.max_drawdown, true) },
            { label: '目标触达率', value: percent(credibility.target_rate) },
            { label: '止损触发率', value: percent(credibility.stop_rate) },
            { label: '复盘样本', value: `${credibility.sample_count || 0}条` }
          ],
          researchSections: [
            { label: '为什么关注', value: detail.research['入选逻辑'] || '-' },
            { label: '最大风险', value: detail.research['最大风险'] || '-' },
            { label: '同板块替代', value: detail.research['同板块替代'] || '-' }
          ],
          sellTriggersText: (report.sell_triggers || []).join('；') || '-',
          loading: false
        })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  addPosition() {
    const detail = this.data.detail
    wx.navigateTo({
      url: `/pages/position/edit?symbol=${encodeURIComponent(detail.symbol)}&name=${encodeURIComponent(detail.name)}`
    })
  },

  addAlert() {
    const detail = this.data.detail
    wx.navigateTo({
      url: `/pages/alert/edit?symbol=${encodeURIComponent(detail.symbol)}&name=${encodeURIComponent(detail.name)}`
    })
  }
})
