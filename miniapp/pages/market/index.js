const { request } = require('../../utils/request')
const { compactNumber, percent } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    freshness: {},
    metrics: [],
    sentiment: null,
    hotspots: [],
    marketMap: [],
    ladder: []
  },

  onLoad() {
    this.loadMarket()
  },

  onPullDownRefresh() {
    this.loadMarket().finally(() => wx.stopPullDownRefresh())
  },

  loadMarket() {
    this.setData({ loading: true, error: '' })
    return request({ url: '/market' })
      .then((data) => {
        const breadth = data.breadth || {}
        const rawSentiment = (data.sentiment || [])[0] || null
        const sentiment = rawSentiment ? Object.assign({}, rawSentiment, {
          score_text: Number(rawSentiment.emotion_score || 0).toFixed(0),
          broken_text: percent(rawSentiment.broken_rate),
          promotion_text: percent(rawSentiment.promotion_rate)
        }) : null
        const hotspots = (data.hotspots || [])
          .filter((item) => item.period === 'daily')
          .slice(0, 12)
          .map((item) => Object.assign({}, item, {
            flow_text: Number(item.net_inflow_yi || 0).toFixed(2) + '亿',
            change_text: Number(item.change_pct || 0).toFixed(2) + '%'
          }))
        const marketMap = (data.market_map || []).slice(0, 15).map((item) => ({
          type: item['类型'] || '-',
          direction: item['方向'] || '-',
          stage: item['阶段'] || '观察',
          leader: item['龙头股'] || '-',
          follower: item['跟风股'] || '-',
          duration: item['主线持续'] || 0,
          score: Number(item['平均评分'] || 0).toFixed(1)
        }))
        this.setData({
          freshness: data.freshness || {},
          metrics: [
            { label: '全市场', value: compactNumber(breadth.total) },
            { label: '上涨占比', value: percent(breadth.up_ratio) },
            { label: '涨跌家数比', value: Number(breadth.adv_dec_ratio || 0).toFixed(2) },
            { label: '市场温度', value: `${breadth.temperature_label || '-'} ${Number(breadth.temperature || 0).toFixed(0)}` }
          ],
          sentiment,
          hotspots,
          marketMap,
          ladder: (data.limit_up_ladder || []).slice(0, 20),
          loading: false
        })
      })
      .catch((error) => this.setData({ loading: false, error: error.message }))
  }
})
