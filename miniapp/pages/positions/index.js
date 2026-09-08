const { request } = require('../../utils/request')
const { price, percent, dateTimeText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    items: [],
    generatingId: 0
  },

  onShow() {
    if (!wx.getStorageSync('miniapp_token')) {
      wx.reLaunch({ url: '/pages/login/index' })
      return
    }
    this.loadPositions()
  },

  onPullDownRefresh() {
    this.loadPositions().finally(() => wx.stopPullDownRefresh())
  },

  decorate(items) {
    return items.map((item) => {
      const plan = item.latest_plan || null
      return Object.assign({}, item, {
        cost_text: price(item.cost_price),
        t_ratio_text: percent(item.t_ratio),
        updated_text: dateTimeText(item.updated_at),
        plan_text: plan ? `${plan.feasibility_label} · ${plan.action_summary}` : '尚未生成今日方案',
        buy_text: plan ? `${price(plan.t_buy_low)}-${price(plan.t_buy_high)}` : '-',
        sell_text: plan ? `${price(plan.t_sell_low)}-${price(plan.t_sell_high)}` : '-',
        current_text: plan ? price(plan.current_price) : '-'
      })
    })
  },

  loadPositions() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/positions' })
      .then((data) => this.setData({ items: this.decorate(data.items || []), loading: false }))
      .catch((error) => this.setData({ loading: false, error: error.message }))
  },

  addPosition() {
    wx.navigateTo({ url: '/pages/position/edit' })
  },

  openStock(event) {
    const symbol = event.currentTarget.dataset.symbol
    wx.navigateTo({ url: `/pages/stock/detail?symbol=${encodeURIComponent(symbol)}` })
  },

  generatePlan(event) {
    const id = Number(event.currentTarget.dataset.id)
    if (this.data.generatingId) return
    this.setData({ generatingId: id, error: '' })
    request({ url: `/positions/${id}/plan`, method: 'POST', timeout: 60000 })
      .then((data) => {
        const plan = data.plan || {}
        wx.showModal({
          title: plan.feasibility_label || '方案已生成',
          content: `${plan.action_summary || ''}\n低吸区：${price(plan.t_buy_low)}-${price(plan.t_buy_high)}\n高抛区：${price(plan.t_sell_low)}-${price(plan.t_sell_high)}`,
          showCancel: false
        })
        return this.loadPositions()
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ generatingId: 0 }))
  },

  removePosition(event) {
    const id = Number(event.currentTarget.dataset.id)
    wx.showModal({
      title: '移出持仓中心',
      content: '历史方案仍会保留，确认移出当前持仓吗？',
      confirmColor: '#b91c1c',
      success: (result) => {
        if (!result.confirm) return
        request({ url: `/positions/${id}`, method: 'DELETE' })
          .then(() => this.loadPositions())
          .catch((error) => this.setData({ error: error.message }))
      }
    })
  }
})
