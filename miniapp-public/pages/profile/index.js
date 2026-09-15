const { request } = require('../../utils/request')
const { listRecords, clearRecords } = require('../../utils/records')

Page({
  data: {
    accountText: '无需登录，本机功能可正常使用',
    profileName: '访客模式',
    loggedIn: false,
    recordCount: 0,
    error: ''
  },

  onShow() {
    const loggedIn = Boolean(wx.getStorageSync('miniapp_token'))
    this.setData({
      accountText: loggedIn ? '正在核对账号状态' : '无需登录，本机功能可正常使用',
      profileName: loggedIn ? '微信用户' : '访客模式',
      loggedIn,
      recordCount: listRecords().length,
      error: ''
    })
    if (!loggedIn) {
      return
    }
    request({ url: '/me' })
      .then((data) => {
        const user = data.user || {}
        this.setData({
          accountText: user.is_wechat_user ? '已登录微信账号' : '已登录',
          profileName: user.real_name || '微信用户',
          loggedIn: true
        })
      })
      .catch((error) => this.setData({
        accountText: '无需登录，本机功能可正常使用',
        profileName: '访客模式',
        loggedIn: false,
        error: error.message
      }))
  },

  openLogin() {
    wx.navigateTo({ url: '/pages/login/index' })
  },

  openSubscription() {
    if (!this.data.loggedIn) {
      wx.showModal({
        title: '登录为自愿选择',
        content: '本机复盘功能无需登录。增值服务需要识别微信账号，是否前往可选登录页面？',
        confirmText: '前往登录',
        cancelText: '继续体验',
        success: (result) => {
          if (result.confirm) this.openLogin()
        }
      })
      return
    }
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
        this.setData({
          accountText: '无需登录，本机功能可正常使用',
          profileName: '访客模式',
          loggedIn: false,
          recordCount: 0,
          error: ''
        })
        wx.showToast({ title: '已注销', icon: 'success' })
      }
    })
  },

  logout() {
    getApp().clearSession()
    this.setData({
      accountText: '无需登录，本机功能可正常使用',
      profileName: '访客模式',
      loggedIn: false,
      error: ''
    })
    wx.showToast({ title: '已退出登录', icon: 'success' })
  }
})
