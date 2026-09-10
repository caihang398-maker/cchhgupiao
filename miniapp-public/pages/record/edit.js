const { findRecord, saveRecord } = require('../../utils/records')

function today() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

Page({
  data: {
    categories: ['学习', '工作', '生活', '其他'],
    categoryIndex: 0,
    form: {
      id: '',
      title: '',
      category: '学习',
      date: today(),
      plan: '',
      result: '',
      conclusion: '',
      score: 0
    }
  },

  onLoad(query) {
    if (!query.id) return
    const record = findRecord(query.id)
    if (!record) return
    const categoryIndex = Math.max(0, this.data.categories.indexOf(record.category))
    this.setData({ form: record, categoryIndex })
  },

  handleInput(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: event.detail.value })
  },

  chooseDate(event) {
    this.setData({ 'form.date': event.detail.value })
  },

  chooseCategory(event) {
    const index = Number(event.detail.value)
    this.setData({ categoryIndex: index, 'form.category': this.data.categories[index] })
  },

  chooseScore(event) {
    this.setData({ 'form.score': Number(event.currentTarget.dataset.score) })
  },

  submit() {
    const form = this.data.form
    if (!String(form.title || '').trim()) {
      wx.showToast({ title: '请填写记录标题', icon: 'none' })
      return
    }
    if (!String(form.plan || '').trim()) {
      wx.showToast({ title: '请填写原计划', icon: 'none' })
      return
    }
    saveRecord(Object.assign({}, form, {
      title: String(form.title).trim(),
      plan: String(form.plan).trim(),
      result: String(form.result || '').trim(),
      conclusion: String(form.conclusion || '').trim()
    }))
    wx.showToast({ title: '已保存', icon: 'success' })
    setTimeout(() => wx.navigateBack(), 500)
  }
})
