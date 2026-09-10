const CLOUD_ENVIRONMENTS = require('./cloud.config').environments

function getEnvironmentVersion() {
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion || 'develop'
  } catch (error) {
    return 'develop'
  }
}

function getRequestTransport() {
  const version = getEnvironmentVersion()
  const cloud = CLOUD_ENVIRONMENTS[version] || CLOUD_ENVIRONMENTS.develop
  if (cloud && cloud.enabled) {
    return {
      mode: 'cloud',
      envId: cloud.envId,
      service: cloud.service
    }
  }
  return {
    mode: 'http',
    baseUrl: 'http://127.0.0.1:8512/api/v1'
  }
}

module.exports = { getEnvironmentVersion, getRequestTransport }
