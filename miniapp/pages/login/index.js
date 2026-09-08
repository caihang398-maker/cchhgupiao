const { request } = require('../../utils/request')

Page({
  data: {
    identifier: '',
    password: '',
    bindToken: '',
    bindingWechat: false,
    automaticLogin: true,
    loading: false,
    error: ''
  },

  onLoad() {
    if (wx.getStorageSync('miniapp_token')) {
      request({ url: '/me' })
        .then((data) => this.routeAfterLogin(data.user || {}))
        .catch((error) => this.setData({ automaticLogin: false, error: error.message }))
      return
    }
    this.wechatLogin({ automatic: true })
  },

  handleIdentifier(event) {
    this.setData({ identifier: event.detail.value })
  },

  handlePassword(event) {
    this.setData({ password: event.detail.value })
  },

  routeAfterLogin(user) {
    if (user.is_admin || user.service_valid) {
      wx.switchTab({ url: '/pages/home/index' })
      return
    }
    wx.reLaunch({ url: '/pages/subscription/index' })
  },

  saveSession(session) {
    getApp().setSession(session)
    if (session.is_new_user) {
      wx.showToast({ title: '体验账号已创建', icon: 'success' })
    }
    this.routeAfterLogin(session.user || {})
  },

  accountLogin() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    const binding = Boolean(this.data.bindToken)
    const url = binding ? '/auth/bind' : '/auth/login'
    const data = {
      identifier: this.data.identifier.trim(),
      password: this.data.password,
      bind_token: this.data.bindToken
    }
    request({ url, method: 'POST', data, auth: false })
      .then((session) => this.saveSession(session))
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ loading: false }))
  },

  wechatLogin(options = {}) {
    if (this.data.loading) return
    this.setData({
      loading: true,
      automaticLogin: Boolean(options.automatic),
      error: ''
    })
    wx.login({
      timeout: 10000,
      success: ({ code }) => {
        request({ url: '/auth/wechat', method: 'POST', data: { code }, auth: false })
          .then((result) => {
            if (result.status === 'binding_required') {
              this.setData({
                bindToken: result.bind_token,
                bindingWechat: true,
                error: ''
              })
              return
            }
            this.saveSession(result)
          })
          .catch((error) => this.setData({ error: error.message }))
          .finally(() => this.setData({ loading: false, automaticLogin: false }))
      },
      fail: () => this.setData({
        loading: false,
        automaticLogin: false,
        error: '未能取得微信登录凭证，可重试或使用已有账号登录'
      })
    })
  }
})
