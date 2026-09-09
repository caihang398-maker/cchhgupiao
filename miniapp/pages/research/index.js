const { request } = require('../../utils/request')

Page({
  data: {
    keyword: '',
    horizon: '',
    loading: true,
    error: '',
    items: [],
    total: 0,
    freshness: { level: '', message: '' }
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadRecommendations()
  },

  onPullDownRefresh() {
    this.loadRecommendations().finally(() => wx.stopPullDownRefresh())
  },

  handleKeyword(event) {
    this.setData({ keyword: event.detail.value })
  },

  selectHorizon(event) {
    this.setData({ horizon: event.currentTarget.dataset.horizon })
    this.loadRecommendations()
  },

  submitSearch() {
    this.loadRecommendations()
  },

  openScreener() {
    wx.navigateTo({ url: '/pages/screener/index' })
  },

  loadRecommendations() {
    this.setData({ loading: true, error: '' })
    const keyword = encodeURIComponent(this.data.keyword.trim())
    const horizon = encodeURIComponent(this.data.horizon)
    return request({ url: `/recommendations?keyword=${keyword}&horizon=${horizon}&page_size=50` })
      .then((data) => this.setData({
        items: data.items || [],
        total: data.total || 0,
        freshness: data.freshness || {},
        loading: false
      }))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  openStock(event) {
    const item = event.detail.item
    wx.navigateTo({ url: `/pages/stock/detail?symbol=${encodeURIComponent(item.symbol)}` })
  }
})
