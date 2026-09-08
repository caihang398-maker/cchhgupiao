const CLOUD_ENVIRONMENTS = require('./cloud.config').environments

const API_BASE_URLS = {
  develop: 'http://127.0.0.1:8512/api/v1',
  trial: 'https://ch.cchhgp.sbs/api/miniapp/v1',
  release: 'https://ch.cchhgp.sbs/api/miniapp/v1'
}

function getEnvironmentVersion() {
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion || 'develop'
  } catch (error) {
    return 'develop'
  }
}

function getApiBaseUrl() {
  const version = getEnvironmentVersion()
  return API_BASE_URLS[version] || API_BASE_URLS.develop
}

function getRequestTransport() {
  const version = getEnvironmentVersion()
  const cloud = CLOUD_ENVIRONMENTS[version] || {}
  if (cloud.enabled && cloud.envId && cloud.service) {
    return {
      mode: 'cloud',
      envId: cloud.envId,
      service: cloud.service
    }
  }
  return {
    mode: 'http',
    baseUrl: API_BASE_URLS[version] || API_BASE_URLS.develop
  }
}

module.exports = {
  API_BASE_URLS,
  CLOUD_ENVIRONMENTS,
  getApiBaseUrl,
  getEnvironmentVersion,
  getRequestTransport
}
