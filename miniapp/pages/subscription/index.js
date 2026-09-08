const { request } = require('../../utils/request')
const { dateTimeText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    statusText: '-',
    serviceValid: false,
    expiryText: '-',
    remainingText: '-',
    plans: [],
    payment: { checklist: [] },
    login: {}
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadSubscription()
  },

  onPullDownRefresh() {
    this.loadSubscription().finally(() => wx.stopPullDownRefresh())
  },

  loadSubscription() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/subscription' })
      .then((data) => {
        const user = data.user || {}
        const labels = {
          active: '服务有效',
          expired: '已到期',
          not_opened: '尚未开通',
          not_started: '尚未开始',
          disabled: '已停用'
        }
        const plans = (data.plans || []).map((item) => ({
          ...item,
          priceText: item.price > 0 ? `¥${Number(item.price).toFixed(2)}` : '免费',
          durationText: item.duration_months === 12 ? '12个月' : `${item.duration_months}个月`,
          recommended: item.plan_code === 'quarter'
        }))
        this.setData({
          statusText: labels[user.service_status] || user.service_status || '-',
          serviceValid: Boolean(user.is_admin || user.service_valid),
          expiryText: user.service_expires_at ? dateTimeText(user.service_expires_at) : '尚未开通',
          remainingText: data.remaining_days === null || data.remaining_days === undefined
            ? '-'
            : `${data.remaining_days}天`,
          plans,
          payment: Object.assign({ checklist: [] }, data.payment || {}),
          login: data.login || {},
          loading: false
        })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  choosePlan(event) {
    const index = Number(event.currentTarget.dataset.index)
    const plan = this.data.plans[index]
    if (!plan) return
    wx.showModal({
      title: plan.plan_name,
      content: this.data.payment.message || '当前由管理员在 PC 端开通或续费。',
      showCancel: false,
      confirmText: '我知道了'
    })
  },

  openProfile() {
    wx.switchTab({ url: '/pages/profile/index' })
  },

  openHome() {
    wx.switchTab({ url: '/pages/home/index' })
  }
})
