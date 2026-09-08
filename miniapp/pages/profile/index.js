const { request } = require('../../utils/request')
const { dateTimeText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    user: {},
    avatarText: '用',
    accountText: '-',
    expiryText: '-',
    statusText: '-'
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadProfile()
  },

  loadProfile() {
    this.setData({ loading: true, error: '' })
    request({ url: '/me' })
      .then((data) => {
        const user = data.user || {}
        const labels = {
          active: '服务有效', expired: '已到期', not_opened: '尚未开通',
          not_started: '尚未开始', disabled: '已停用'
        }
        this.setData({
          user,
          avatarText: String(user.real_name || '用').slice(0, 1),
          accountText: user.is_wechat_user
            ? '微信快捷登录'
            : (user.mobile || user.login_name || '本机调试账号'),
          expiryText: user.service_expires_at ? dateTimeText(user.service_expires_at) : '本机调试',
          statusText: labels[user.service_status] || user.service_status || '-',
          loading: false
        })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  openMarket() {
    wx.navigateTo({ url: '/pages/market/index' })
  },

  openPaper() {
    wx.navigateTo({ url: '/pages/paper/index' })
  },

  openSubscription() {
    wx.navigateTo({ url: '/pages/subscription/index' })
  },

  openScreener() {
    wx.navigateTo({ url: '/pages/screener/index' })
  },

  openDataStatus() {
    wx.navigateTo({ url: '/pages/data-status/index' })
  },

  openReview() {
    wx.navigateTo({ url: '/pages/review/index' })
  },

  openRisk() {
    wx.navigateTo({ url: '/pages/risk/index' })
  },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确认退出当前账号吗？',
      success: (result) => {
        if (!result.confirm) return
        getApp().clearSession()
        wx.reLaunch({ url: '/pages/login/index' })
      }
    })
  }
})
