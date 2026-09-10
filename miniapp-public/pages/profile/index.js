const { request } = require('../../utils/request')
const { listRecords, clearRecords } = require('../../utils/records')

Page({
  data: {
    accountText: '微信快捷登录',
    recordCount: 0,
    error: ''
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.setData({ recordCount: listRecords().length, error: '' })
    request({ url: '/me' })
      .then((data) => {
        const user = data.user || {}
        this.setData({ accountText: user.is_wechat_user ? '微信快捷登录' : (user.real_name || '微信用户') })
      })
      .catch((error) => this.setData({ error: error.message }))
  },

  openSubscription() {
    wx.navigateTo({ url: '/pages/subscription/index' })
  },

  openLegal(event) {
    wx.navigateTo({ url: `/pages/legal/index?type=${event.currentTarget.dataset.type}` })
  },

  contact() {},

  clearLocalData() {
    wx.showModal({
      title: '清除本机数据',
      content: '将删除本机全部复盘记录且无法恢复，确认继续吗？',
      success: (result) => {
        if (!result.confirm) return
        clearRecords()
        this.setData({ recordCount: 0 })
        wx.showToast({ title: '已清除', icon: 'success' })
      }
    })
  },

  deleteAccount() {
    wx.showModal({
      title: '注销账号',
      content: '将清除本机全部记录和登录状态。当前版本不会在业务数据库建立个人档案，操作完成后可重新登录使用。',
      confirmText: '确认注销',
      confirmColor: '#b91c1c',
      success: (result) => {
        if (!result.confirm) return
        clearRecords()
        getApp().clearSession()
        wx.reLaunch({ url: '/pages/login/index' })
      }
    })
  },

  logout() {
    getApp().clearSession()
    wx.reLaunch({ url: '/pages/login/index' })
  }
})
