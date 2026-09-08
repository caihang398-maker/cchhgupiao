const { request } = require('../../utils/request')
const { dateText, dateTimeText, compactNumber } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    ready: false,
    checkedText: '-',
    recommendation: {},
    market: {},
    marketMetrics: [],
    items: []
  },

  onLoad() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadStatus()
  },

  onPullDownRefresh() {
    this.loadStatus().finally(() => wx.stopPullDownRefresh())
  },

  decorateItems(items) {
    return items.map((item) => Object.assign({}, item, {
      status_class: item.status === '正常'
        ? 'status-good'
        : item.status.indexOf('异常') >= 0 || item.status.indexOf('无') >= 0
          ? 'status-risk'
          : 'status-warning'
    }))
  },

  loadStatus() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/data-status' })
      .then((data) => {
        const market = data.market || {}
        this.setData({
          ready: Boolean(data.ready_for_decision),
          checkedText: dateTimeText(data.checked_at),
          recommendation: data.recommendation_freshness || {},
          market,
          marketMetrics: [
            { label: '报价日期', value: dateText(market.quote_date) },
            { label: '行情来源', value: market.source_label || '-' },
            { label: '覆盖股票', value: compactNumber(market.row_count) },
            { label: '有效价格', value: compactNumber(market.valid_price_count) }
          ],
          items: this.decorateItems(data.items || []),
          loading: false
        })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  }
})
