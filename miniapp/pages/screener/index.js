const { request } = require('../../utils/request')

const CONDITIONS = [
  '主力资金连续流入',
  '技术评分较高',
  '处于买点附近',
  '低估值正常区间',
  '现金流良好',
  '分红稳定',
  '市场主线方向',
  '高可信度推荐'
]

const DEFAULT_CONDITIONS = ['技术评分较高', '处于买点附近', '市场主线方向']

function optionalNumber(value) {
  const text = String(value == null ? '' : value).trim()
  if (!text) return null
  const number = Number(text)
  return Number.isFinite(number) ? number : NaN
}

Page({
  data: {
    conditionOptions: CONDITIONS.map((name) => ({
      name,
      checked: DEFAULT_CONDITIONS.indexOf(name) >= 0
    })),
    selectedConditions: DEFAULT_CONDITIONS,
    minScore: 72,
    maxPositionPercent: 35,
    minPrice: '',
    maxPrice: '',
    randomPick: false,
    randomCount: 10,
    keyword: '',
    loading: false,
    error: '',
    freshness: {},
    poolCount: 0,
    resultCount: 0,
    items: []
  },

  onLoad() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.runScreener()
  },

  selectConditions(event) {
    this.setData({ selectedConditions: event.detail.value })
  },

  changeScore(event) {
    this.setData({ minScore: Number(event.detail.value) })
  },

  changePosition(event) {
    this.setData({ maxPositionPercent: Number(event.detail.value) })
  },

  handlePrice(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [field]: event.detail.value })
  },

  toggleRandomPick(event) {
    this.setData({ randomPick: Boolean(event.detail.value) })
  },

  changeRandomCount(event) {
    this.setData({ randomCount: Number(event.detail.value) })
  },

  handleKeyword(event) {
    this.setData({ keyword: event.detail.value })
  },

  runScreener() {
    if (this.data.loading) return
    const minPrice = optionalNumber(this.data.minPrice)
    const maxPrice = optionalNumber(this.data.maxPrice)
    if (Number.isNaN(minPrice) || Number.isNaN(maxPrice) || (minPrice !== null && minPrice <= 0) || (maxPrice !== null && maxPrice <= 0)) {
      this.setData({ error: '股价范围请输入大于0的数字。' })
      return
    }
    if (minPrice !== null && maxPrice !== null && maxPrice < minPrice) {
      this.setData({ error: '最高股价不能低于最低股价。' })
      return
    }
    this.setData({ loading: true, error: '' })
    request({
      url: '/screener',
      method: 'POST',
      data: {
        conditions: this.data.selectedConditions,
        min_score: this.data.minScore,
        max_position: this.data.maxPositionPercent / 100,
        min_price: minPrice,
        max_price: maxPrice,
        random_pick: this.data.randomPick,
        random_count: this.data.randomCount,
        keyword: this.data.keyword.trim()
      }
    })
      .then((data) => this.setData({
        freshness: data.freshness || {},
        poolCount: data.pool_count || 0,
        resultCount: data.result_count || 0,
        items: data.items || [],
        loading: false
      }))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  openStock(event) {
    const item = event.detail.item
    wx.navigateTo({ url: `/pages/stock/detail?symbol=${encodeURIComponent(item.symbol)}` })
  }
})
