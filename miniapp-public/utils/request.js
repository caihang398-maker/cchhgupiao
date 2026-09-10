const { getRequestTransport } = require('../config')

const TRANSIENT_STATUS = [502, 503, 504]

function parsePayload(data) {
  if (typeof data !== 'string') return data || {}
  try {
    return JSON.parse(data)
  } catch (error) {
    return {}
  }
}

function request(options) {
  const token = wx.getStorageSync('miniapp_token')
  const transport = getRequestTransport()
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.header || {})
  if (options.auth !== false && token) headers.Authorization = `Bearer ${token}`
  const method = String(options.method || 'GET').toUpperCase()
  const retryDelays = method === 'GET' || String(options.url).indexOf('/auth/') === 0
    ? [1200, 2500, 4500]
    : []

  return new Promise((resolve, reject) => {
    const send = (attempt) => {
      const common = {
        method,
        data: options.data || {},
        header: headers,
        timeout: options.timeout || 60000,
        success(response) {
          const payload = parsePayload(response.data)
          if (TRANSIENT_STATUS.indexOf(response.statusCode) >= 0 && retryDelays[attempt] != null) {
            setTimeout(() => send(attempt + 1), retryDelays[attempt])
            return
          }
          if (response.statusCode >= 200 && response.statusCode < 300 && payload.ok) {
            resolve(payload.data)
            return
          }
          if (response.statusCode === 401 && options.auth !== false) {
            getApp().clearSession()
            wx.reLaunch({ url: '/pages/login/index' })
          }
          const detail = payload.error || {}
          const error = new Error(detail.message || `请求失败（${response.statusCode}）`)
          error.code = detail.code || 'request_failed'
          reject(error)
        },
        fail(error) {
          if (retryDelays[attempt] != null) {
            setTimeout(() => send(attempt + 1), retryDelays[attempt])
            return
          }
          reject(new Error(error.errMsg && error.errMsg.indexOf('timeout') >= 0
            ? '服务响应超时，请稍后重试'
            : '暂时无法连接服务，请检查网络后重试'))
        }
      }
      if (transport.mode === 'cloud') {
        wx.cloud.callContainer(Object.assign({}, common, {
          config: { env: transport.envId },
          path: `/api/v1${options.url}`,
          header: Object.assign({}, headers, { 'X-WX-SERVICE': transport.service })
        }))
        return
      }
      wx.request(Object.assign({}, common, { url: `${transport.baseUrl}${options.url}` }))
    }
    send(0)
  })
}

module.exports = { request }
