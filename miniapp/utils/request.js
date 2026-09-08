const { getRequestTransport } = require('../config')

let redirecting = false
let redirectingToSubscription = false

function redirectToLogin() {
  if (redirecting) return
  redirecting = true
  getApp().clearSession()
  wx.reLaunch({
    url: '/pages/login/index',
    complete() {
      setTimeout(() => {
        redirecting = false
      }, 500)
    }
  })
}

function parsePayload(data) {
  if (typeof data !== 'string') return data || {}
  try {
    return JSON.parse(data)
  } catch (error) {
    return {}
  }
}

function handleResponse(response, options, resolve, reject) {
  const payload = parsePayload(response.data)
  if (response.statusCode >= 200 && response.statusCode < 300 && payload.ok) {
    resolve(payload.data)
    return
  }
  const error = payload.error || {}
  const message = error.message || `请求失败（${response.statusCode}）`
  if (response.statusCode === 401 && options.auth !== false) {
    redirectToLogin()
  }
  if (
    response.statusCode === 403 &&
    error.code === 'subscription_required' &&
    options.url !== '/subscription' &&
    !redirectingToSubscription
  ) {
    redirectingToSubscription = true
    wx.reLaunch({
      url: '/pages/subscription/index',
      complete() {
        setTimeout(() => {
          redirectingToSubscription = false
        }, 500)
      }
    })
  }
  const requestError = new Error(message)
  requestError.code = error.code || 'request_failed'
  requestError.statusCode = response.statusCode
  reject(requestError)
}

function failureMessage(error, cloudMode) {
  if (error.errMsg && error.errMsg.includes('timeout')) {
    return '服务器响应超时，请稍后重试'
  }
  if (cloudMode) {
    return '云服务连接失败，请检查云环境和服务配置'
  }
  return '无法连接服务器，请检查网络和接口地址'
}

function request(options) {
  const token = wx.getStorageSync('miniapp_token')
  const headers = Object.assign(
    { 'Content-Type': 'application/json' },
    options.header || {}
  )
  if (options.auth !== false && token) {
    headers.Authorization = `Bearer ${token}`
  }
  const transport = getRequestTransport()

  return new Promise((resolve, reject) => {
    const commonOptions = {
      method: options.method || 'GET',
      data: options.data || {},
      header: headers,
      timeout: options.timeout || 60000,
      success(response) {
        handleResponse(response, options, resolve, reject)
      },
      fail(error) {
        reject(new Error(failureMessage(error, transport.mode === 'cloud')))
      }
    }

    if (transport.mode === 'cloud') {
      if (!wx.cloud || typeof wx.cloud.callContainer !== 'function') {
        reject(new Error('当前微信基础库不支持云托管调用，请升级微信后重试'))
        return
      }
      wx.cloud.callContainer(Object.assign({}, commonOptions, {
        config: { env: transport.envId },
        path: `/api/v1${options.url}`,
        header: Object.assign({}, headers, {
          'X-WX-SERVICE': transport.service
        })
      }))
      return
    }

    wx.request(Object.assign({}, commonOptions, {
      url: `${transport.baseUrl}${options.url}`
    }))
  })
}

module.exports = { request }
