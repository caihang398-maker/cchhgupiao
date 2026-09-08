const { request } = require('../../utils/request')

Page({
  data: {
    form: {
      symbol: '',
      name: '',
      alert_type: '到达买点',
      comparator: '进入区间',
      threshold_value: '',
      note: ''
    },
    types: ['到达买点', '跌破止损', '主力资金连续流入', '板块主线升温', '龙头切换', '情绪退潮'],
    typeIndex: 0,
    saving: false,
    error: ''
  },

  onLoad(options) {
    this.setData({
      'form.symbol': decodeURIComponent(options.symbol || ''),
      'form.name': decodeURIComponent(options.name || '')
    })
  },

  handleInput(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: event.detail.value })
  },

  selectType(event) {
    const index = Number(event.detail.value)
    const type = this.data.types[index]
    const comparator = type === '到达买点' ? '进入区间' : type === '跌破止损' ? '<=' : '>='
    this.setData({ typeIndex: index, 'form.alert_type': type, 'form.comparator': comparator })
  },

  save() {
    if (this.data.saving) return
    const data = Object.assign({}, this.data.form, {
      threshold_value: this.data.form.threshold_value === '' ? null : Number(this.data.form.threshold_value)
    })
    this.setData({ saving: true, error: '' })
    request({ url: '/alerts', method: 'POST', data })
      .then((result) => {
        const duplicate = result && result.created === false
        wx.showToast({ title: duplicate ? '规则已存在' : '预警已保存', icon: duplicate ? 'none' : 'success' })
        setTimeout(() => wx.navigateBack(), 500)
      })
      .catch((error) => this.setData({ error: error.message }))
      .finally(() => this.setData({ saving: false }))
  }
})
