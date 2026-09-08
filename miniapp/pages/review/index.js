const { request } = require('../../utils/request')
const { percent, dateText } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    summaries: [],
    reports: []
  },

  onLoad() {
    this.loadReview()
  },

  onPullDownRefresh() {
    this.loadReview().finally(() => wx.stopPullDownRefresh())
  },

  loadReview() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/review' })
      .then((data) => {
        const summaries = (data.summary || []).map((item) => ({
          horizon: item['周期'],
          count: item['推荐数'],
          t1: percent(item['推荐后1日平均收益'], true),
          t5: percent(item['推荐后5日平均收益'], true),
          t20: percent(item['推荐后20日平均收益'], true),
          win5: percent(item['推荐后5日上涨率']),
          drawdown: percent(item['最差最大回撤'], true),
          stop: percent(item['止损触发率']),
          target: percent(item['目标一命中率'])
        }))
        const reports = (data.daily_reports || []).map((item) => Object.assign({}, item, {
          date_text: dateText(item.trade_date)
        }))
        this.setData({ summaries, reports, loading: false })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  }
})
