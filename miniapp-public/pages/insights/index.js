const { listRecords, summary } = require('../../utils/records')

Page({
  data: {
    summary: {},
    categories: []
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    const records = listRecords()
    const counts = {}
    records.forEach((item) => {
      const key = item.category || '其他'
      counts[key] = (counts[key] || 0) + 1
    })
    const categories = Object.keys(counts)
      .map((name) => ({ name, count: counts[name] }))
      .sort((a, b) => b.count - a.count)
    this.setData({ summary: summary(records), categories })
  }
})
