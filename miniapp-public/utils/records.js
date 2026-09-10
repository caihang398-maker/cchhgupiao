const STORAGE_KEY = 'personal_review_records_v1'
const MAX_RECORDS = 500

function listRecords() {
  const value = wx.getStorageSync(STORAGE_KEY)
  if (!Array.isArray(value)) return []
  return value.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)))
}

function findRecord(id) {
  return listRecords().find((item) => item.id === id) || null
}

function saveRecord(record) {
  const records = listRecords()
  const now = new Date().toISOString()
  const item = Object.assign({}, record, {
    id: record.id || `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    updatedAt: now,
    createdAt: record.createdAt || now
  })
  const index = records.findIndex((current) => current.id === item.id)
  if (index >= 0) records[index] = item
  else records.unshift(item)
  wx.setStorageSync(STORAGE_KEY, records.slice(0, MAX_RECORDS))
  return item
}

function removeRecord(id) {
  wx.setStorageSync(STORAGE_KEY, listRecords().filter((item) => item.id !== id))
}

function clearRecords() {
  wx.removeStorageSync(STORAGE_KEY)
}

function summary(records = listRecords()) {
  const completed = records.filter((item) => String(item.result || '').trim())
  const scored = records.filter((item) => Number(item.score) > 0)
  const averageScore = scored.length
    ? scored.reduce((total, item) => total + Number(item.score), 0) / scored.length
    : 0
  const now = Date.now()
  const recent = records.filter((item) => {
    const timestamp = Date.parse(item.date)
    return Number.isFinite(timestamp) && now - timestamp <= 30 * 86400000
  })
  return {
    total: records.length,
    completed: completed.length,
    completionRate: records.length ? Math.round(completed.length * 100 / records.length) : 0,
    averageScore: averageScore ? averageScore.toFixed(1) : '-',
    recentCount: recent.length
  }
}

module.exports = { listRecords, findRecord, saveRecord, removeRecord, clearRecords, summary }
