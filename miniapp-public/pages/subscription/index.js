const { getEnvironmentVersion } = require('../../config')
const { request } = require('../../utils/request')

Page({
  data: {
    loading: true,
    paying: false,
    error: '',
    payment: { enabled: false, checklist: [] },
    plans: [],
    agreed: false,
    showTechnical: getEnvironmentVersion() !== 'release'
  },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  load() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/subscription' })
      .then((data) => this.setData({
        loading: false,
        payment: Object.assign({ enabled: false, checklist: [] }, data.payment || {}),
        plans: (data.plans || []).map((item) => Object.assign({}, item, {
          priceText: `¥${Number(item.price || 0).toFixed(2)}`,
          featuresText: (item.features || []).join(' · ')
        }))
      }))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  toggleAgreement() {
    this.setData({ agreed: !this.data.agreed })
  },

  openPaymentRules() {
    wx.navigateTo({ url: '/pages/legal/index?type=payment' })
  },

  choosePlan(event) {
    const plan = this.data.plans[Number(event.currentTarget.dataset.index)]
    if (!plan) return
    if (!this.data.payment.enabled) {
      wx.showModal({ title: '暂未开放购买', content: this.data.payment.message || '支付能力仍在验收中。', showCancel: false })
      return
    }
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意购买说明', icon: 'none' })
      return
    }
    this.startPayment(plan)
  },

  startPayment(plan) {
    if (this.data.paying) return
    this.setData({ paying: true })
    wx.login({
      success: ({ code }) => {
        request({ url: '/subscription/orders', method: 'POST', data: { plan_code: plan.plan_code, code } })
          .then((order) => new Promise((resolve, reject) => {
            wx.requestVirtualPayment(Object.assign({}, order.pay_data, { success: resolve, fail: reject }))
          }))
          .then(() => {
            wx.showModal({ title: '正在确认', content: '平台正在核对订单并开通服务，请稍后下拉刷新。', showCancel: false })
          })
          .catch((error) => {
            const canceled = String(error.errMsg || error.message || '').indexOf('cancel') >= 0
            if (!canceled) wx.showModal({ title: '购买未完成', content: error.message || '请稍后重试', showCancel: false })
          })
          .finally(() => this.setData({ paying: false }))
      },
      fail: () => this.setData({ paying: false, error: '未能取得微信登录凭证' })
    })
  }
})
