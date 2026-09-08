const { getRequestTransport } = require('./config')

App({
  globalData: {
    user: null
  },

  onLaunch() {
    const transport = getRequestTransport()
    if (transport.mode === 'cloud' && wx.cloud) {
      try {
        wx.cloud.init({
          env: transport.envId,
          traceUser: true
        })
      } catch (error) {
        console.warn('云开发初始化失败', error)
      }
    }
    const user = wx.getStorageSync('miniapp_user')
    if (user) {
      this.globalData.user = user
    }
  },

  setSession(session) {
    wx.setStorageSync('miniapp_token', session.token)
    wx.setStorageSync('miniapp_user', session.user)
    this.globalData.user = session.user
  },

  clearSession() {
    wx.removeStorageSync('miniapp_token')
    wx.removeStorageSync('miniapp_user')
    this.globalData.user = null
  }
})
