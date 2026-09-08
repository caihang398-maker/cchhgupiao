const { request } = require('../../utils/request')

Page({
  data: {
    form: {
      symbol: '',
      name: '',
      cost_price: '',
      shares: 1000,
      available_cash: 0,
      account_assets: 0,
      sellable_shares: 1000,
      t_ratio: 0.2,
      t_preference: '自动判断',
      note: ''
    },
    preferences: ['自动判断', '先卖后买', '先买后卖'],
    ratios: [10, 15, 20, 25, 30, 40, 50],
    preferenceIndex: 0,
    ratioIndex: 2,
    saving: false,
    error: ''
  },

  onLoad(options) {
    const form = Object.assign({}, this.data.form, {
      symbol: decodeURIComponent(options.symbol || ''),
      name: decodeURIComponent(options.name || '')
    })
    this.setData({ form })
  },

  handleInput(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: event.detail.value })
  },

  selectPreference(event) {
    const index = Number(event.detail.value)
    this.setData({ preferenceIndex: index, 'form.t_preference': this.data.preferences[index] })
  },

  selectRatio(event) {
    const index = Number(event.detail.value)
    this.setData({ ratioIndex: index, 'form.t_ratio': this.data.ratios[index] / 100 })
  },

  save() {
    if (this.data.saving) return
    const form = Object.assign({}, this.data.form, {
      cost_price: Number(this.data.form.cost_price),
      shares: Number(this.data.form.shares),
      available_cash: Number(this.data.form.available_cash),
      account_assets: Number(this.data.form.account_assets),
      sellable_shares: Number(this.data.form.sellable_shares),
      t_ratio: Number(this.data.form.t_ratio)
    })
    this.setData({ saving: true, error: '' })
    request({ url: '/positions', method: 'POST', data: form })
      .then(() => {
        wx.showToast({ title: '持仓已保存', icon: 'success' })
        setTimeout(() => wx.navigateBack(), 500)
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ saving: false }))
  }
})
