const { listRecords, summary } = require('../../utils/records')

Page({
  data: {
    summary: {},
    recent: []
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.refresh()
  },

  onPullDownRefresh() {
    this.refresh()
    wx.stopPullDownRefresh()
  },

  refresh() {
    const records = listRecords()
    this.setData({ summary: summary(records), recent: records.slice(0, 4) })
  },

  createRecord() {
    wx.navigateTo({ url: '/pages/record/edit' })
  },

  openRecord(event) {
    wx.navigateTo({ url: `/pages/record/edit?id=${event.currentTarget.dataset.id}` })
  },

  openRecords() {
    wx.switchTab({ url: '/pages/records/index' })
  }
})
