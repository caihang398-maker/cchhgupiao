const { request } = require('../../utils/request')
const { compactNumber, dateText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    dataDate: '-',
    freshness: { level: '', message: '' },
    summary: {},
    marketMetrics: [],
    actions: [],
    sentimentText: '暂无情绪数据',
    recommendations: [],
    selectedHorizon: 'all',
    visibleRecommendations: []
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadHome()
  },

  onPullDownRefresh() {
    this.loadHome().finally(() => wx.stopPullDownRefresh())
  },

  loadHome() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/home?limit=30' })
      .then((data) => {
        const breadth = (data.market && data.market.breadth) || {}
        const sentiment = data.sentiment || {}
        const metrics = [
          { label: '上涨 / 下跌', value: `${compactNumber(breadth.up)} / ${compactNumber(breadth.down)}` },
          { label: '市场温度', value: `${breadth.temperature_label || '-'} ${Number(breadth.temperature || 0).toFixed(0)}` },
          { label: '涨停 / 跌停', value: `${breadth.limit_up || 0} / ${breadth.limit_down || 0}` },
          { label: '成交额', value: compactNumber(breadth.turnover) }
        ]
        this.setData({
          dataDate: dateText(data.freshness && data.freshness.data_date),
          freshness: data.freshness || {},
          summary: data.summary || {},
          marketMetrics: metrics,
          actions: data.actions || [],
          sentimentText: sentiment.emotion_stage
            ? `${sentiment.emotion_stage} ${Number(sentiment.emotion_score || 0).toFixed(0)}分`
            : '暂无情绪数据',
          recommendations: data.recommendations || [],
          loading: false
        })
        this.applyFilter(this.data.selectedHorizon)
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  selectHorizon(event) {
    const horizon = event.currentTarget.dataset.horizon
    this.setData({ selectedHorizon: horizon })
    this.applyFilter(horizon)
  },

  applyFilter(horizon) {
    const items = horizon === 'all'
      ? this.data.recommendations
      : this.data.recommendations.filter((item) => item.horizon === horizon)
    this.setData({ visibleRecommendations: items })
  },

  openStock(event) {
    const item = event.detail.item
    wx.navigateTo({ url: `/pages/stock/detail?symbol=${encodeURIComponent(item.symbol)}` })
  },

  openMarket() {
    wx.navigateTo({ url: '/pages/market/index' })
  },

  openReview() {
    wx.navigateTo({ url: '/pages/review/index' })
  },

  openAction(event) {
    const target = event.currentTarget.dataset.target
    const routes = {
      'data-status': '/pages/data-status/index',
      positions: '/pages/positions/index',
      alerts: '/pages/alerts/index',
      research: '/pages/research/index'
    }
    const route = routes[target]
    if (!route || target === 'home') return
    if (['positions', 'alerts', 'research'].includes(target)) {
      wx.switchTab({ url: route })
      return
    }
    wx.navigateTo({ url: route })
  }
})
