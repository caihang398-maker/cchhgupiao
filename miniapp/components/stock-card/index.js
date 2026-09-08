const { price, percent } = require('../../utils/format')

Component({
  properties: {
    item: { type: Object, value: {} }
  },
  data: {
    display: {}
  },
  observers: {
    item(value) {
      const credibility = value.credibility || {}
      this.setData({
        display: {
          close: price(value.close),
          position: percent(value.position_pct),
          credibility: Number(credibility.score || 0).toFixed(0)
        }
      })
    }
  },
  methods: {
    handleTap() {
      this.triggerEvent('select', { item: this.properties.item })
    }
  }
})
