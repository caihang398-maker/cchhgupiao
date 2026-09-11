const { request } = require('../../utils/request')

Page({
  data: {
    loading: false,
    error: '',
    agreed: false
  },

  onShow() {
    if (wx.getStorageSync('miniapp_token')) {
      wx.switchTab({ url: '/pages/home/index' })
    }
  },

  login() {
    if (this.data.loading) return
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意服务协议和隐私说明', icon: 'none' })
      return
    }
    this.setData({ loading: true, error: '' })
    wx.login({
      success: ({ code }) => {
        request({
          url: '/auth/wechat',
          method: 'POST',
          auth: false,
          data: { code: code || '' },
          timeout: 30000
        })
          .then((session) => {
            if (session.status !== 'authenticated' || !session.token) {
              throw new Error('当前账号暂时无法登录')
            }
            getApp().setSession(session)
            wx.switchTab({ url: '/pages/home/index' })
          })
          .catch((error) => this.setData({ loading: false, error: error.message }))
      },
      fail: () => this.setData({ loading: false, error: '未能取得微信登录凭证，请重试' })
    })
  },

  openPrivacy() {
    wx.navigateTo({ url: '/pages/legal/index?type=privacy' })
  },

  openAgreement() {
    wx.navigateTo({ url: '/pages/legal/index?type=agreement' })
  },

  toggleAgreement() {
    this.setData({ agreed: !this.data.agreed })
  }
})
