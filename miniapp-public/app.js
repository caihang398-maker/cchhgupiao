const { getRequestTransport } = require('./config')

App({
  globalData: {
    user: null
  },

  onLaunch() {
    const transport = getRequestTransport()
    if (transport.mode === 'cloud' && wx.cloud) {
      wx.cloud.init({ env: transport.envId, traceUser: true })
    }
    this.globalData.user = wx.getStorageSync('miniapp_user') || null
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
