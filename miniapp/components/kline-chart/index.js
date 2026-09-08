Component({
  properties: {
    bars: { type: Array, value: [] }
  },
  observers: {
    bars(value) {
      if (value && value.length) {
        setTimeout(() => this.draw(value), 80)
      }
    }
  },
  lifetimes: {
    ready() {
      if (this.properties.bars.length) this.draw(this.properties.bars)
    }
  },
  methods: {
    draw(source) {
      const bars = source.slice(-80)
      if (!bars.length) return
      const system = wx.getSystemInfoSync()
      const width = Math.max(300, system.windowWidth - 28)
      const height = 260
      const padding = { left: 10, right: 44, top: 18, bottom: 28 }
      const plotWidth = width - padding.left - padding.right
      const plotHeight = height - padding.top - padding.bottom
      const values = []
      bars.forEach((bar) => {
        ;['high', 'low', 'ma5', 'ma20'].forEach((key) => {
          const value = Number(bar[key])
          if (Number.isFinite(value)) values.push(value)
        })
      })
      const minimum = Math.min.apply(null, values)
      const maximum = Math.max.apply(null, values)
      const range = Math.max(0.01, maximum - minimum)
      const y = (value) => padding.top + (maximum - Number(value)) / range * plotHeight
      const step = plotWidth / bars.length
      const bodyWidth = Math.max(2, Math.min(7, step * 0.62))
      const context = wx.createCanvasContext('klineCanvas', this)

      context.setFillStyle('#ffffff')
      context.fillRect(0, 0, width, height)
      context.setStrokeStyle('#e5e7eb')
      context.setLineWidth(0.5)
      for (let index = 0; index <= 4; index += 1) {
        const lineY = padding.top + plotHeight * index / 4
        context.beginPath()
        context.moveTo(padding.left, lineY)
        context.lineTo(width - padding.right, lineY)
        context.stroke()
        context.setFillStyle('#6b7280')
        context.setFontSize(10)
        context.fillText((maximum - range * index / 4).toFixed(2), width - padding.right + 5, lineY + 3)
      }

      bars.forEach((bar, index) => {
        const open = Number(bar.open)
        const close = Number(bar.close)
        const high = Number(bar.high)
        const low = Number(bar.low)
        const x = padding.left + step * index + step / 2
        const color = close >= open ? '#dc2626' : '#059669'
        context.setStrokeStyle(color)
        context.setFillStyle(color)
        context.setLineWidth(1)
        context.beginPath()
        context.moveTo(x, y(high))
        context.lineTo(x, y(low))
        context.stroke()
        const top = Math.min(y(open), y(close))
        const bodyHeight = Math.max(1, Math.abs(y(open) - y(close)))
        context.fillRect(x - bodyWidth / 2, top, bodyWidth, bodyHeight)
      })

      const drawLine = (key, color) => {
        context.setStrokeStyle(color)
        context.setLineWidth(1.2)
        context.beginPath()
        let started = false
        bars.forEach((bar, index) => {
          const value = Number(bar[key])
          if (!Number.isFinite(value)) return
          const x = padding.left + step * index + step / 2
          if (!started) {
            context.moveTo(x, y(value))
            started = true
          } else {
            context.lineTo(x, y(value))
          }
        })
        context.stroke()
      }
      drawLine('ma5', '#2563eb')
      drawLine('ma20', '#d97706')

      context.setFillStyle('#6b7280')
      context.setFontSize(10)
      const firstDate = String(bars[0].date || '').slice(5, 10)
      const lastDate = String(bars[bars.length - 1].date || '').slice(5, 10)
      context.fillText(firstDate, padding.left, height - 8)
      context.fillText(lastDate, width - padding.right - 34, height - 8)
      context.setFillStyle('#2563eb')
      context.fillText('5日线', padding.left + 80, height - 8)
      context.setFillStyle('#d97706')
      context.fillText('20日线', padding.left + 130, height - 8)
      context.draw()
    }
  }
})
