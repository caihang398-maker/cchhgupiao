const { listRecords, removeRecord } = require('../../utils/records')

Page({
  data: {
    records: [],
    filter: '全部',
    filters: ['全部', '待复盘', '已复盘']
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.refresh()
  },

  refresh() {
    const all = listRecords()
    const records = all.filter((item) => {
      if (this.data.filter === '待复盘') return !String(item.result || '').trim()
      if (this.data.filter === '已复盘') return Boolean(String(item.result || '').trim())
      return true
    })
    this.setData({ records })
  },

  chooseFilter(event) {
    this.setData({ filter: event.currentTarget.dataset.value }, () => this.refresh())
  },

  createRecord() {
    wx.navigateTo({ url: '/pages/record/edit' })
  },

  openRecord(event) {
    wx.navigateTo({ url: `/pages/record/edit?id=${event.currentTarget.dataset.id}` })
  },

  deleteRecord(event) {
    const id = event.currentTarget.dataset.id
    wx.showModal({
      title: '删除记录',
      content: '删除后无法恢复，确认继续吗？',
      success: (result) => {
        if (!result.confirm) return
        removeRecord(id)
        this.refresh()
      }
    })
  }
})
