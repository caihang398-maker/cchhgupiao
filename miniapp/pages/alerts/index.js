const { request } = require('../../utils/request')
const { dateTimeText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    evaluating: false,
    quickScope: '',
    error: '',
    rules: [],
    events: []
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadAlerts()
  },

  onPullDownRefresh() {
    this.loadAlerts().finally(() => wx.stopPullDownRefresh())
  },

  decorateRules(items) {
    return items.map((item) => Object.assign({}, item, {
      enabled_bool: Boolean(item.enabled),
      target_text: item.symbol ? `${item.name || ''} ${item.symbol}` : '全市场'
    }))
  },

  decorateEvents(items) {
    return items.map((item) => Object.assign({}, item, {
      time_text: dateTimeText(item.created_at),
      severity_class: item.severity === '风险' ? 'event-risk' : item.severity === '机会' ? 'event-opportunity' : ''
    }))
  },

  loadAlerts() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/alerts' })
      .then((data) => this.setData({
        rules: this.decorateRules(data.rules || []),
        events: this.decorateEvents(data.events || []),
        loading: false
      }))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  addAlert() {
    wx.navigateTo({ url: '/pages/alert/edit' })
  },

  createQuickAlerts(event) {
    const scope = event.currentTarget.dataset.scope
    if (this.data.quickScope) return
    this.setData({ quickScope: scope, error: '' })
    request({ url: '/alerts/quick', method: 'POST', data: { scope, limit: 5 } })
      .then((data) => {
        wx.showToast({
          title: data.created ? `新增${data.created}条规则` : '规则已存在',
          icon: 'none'
        })
        return this.loadAlerts()
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ quickScope: '' }))
  },

  toggleRule(event) {
    const id = Number(event.currentTarget.dataset.id)
    const enabled = event.detail.value
    request({ url: `/alerts/${id}`, method: 'PATCH', data: { enabled } })
      .then(() => this.loadAlerts())
      .catch((error) => this.setData({ error: error.message }))
  },

  evaluate() {
    if (this.data.evaluating) return
    this.setData({ evaluating: true, error: '' })
    request({ url: '/alerts/evaluate', method: 'POST', timeout: 60000 })
      .then((data) => {
        wx.showToast({ title: data.created ? `触发${data.created}条` : '暂无触发', icon: 'none' })
        return this.loadAlerts()
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ evaluating: false }))
  }
})
