function price(value) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '-'
}

function percent(value, signed) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  const prefix = signed && number > 0 ? '+' : ''
  return `${prefix}${(number * 100).toFixed(2)}%`
}

function compactNumber(value) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(1)}亿`
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(1)}万`
  return number.toLocaleString()
}

function dateText(value) {
  if (!value) return '-'
  const text = String(value).slice(0, 10)
  const parts = text.split('-')
  if (parts.length !== 3) return text
  return `${parts[0]}年${parts[1]}月${parts[2]}日`
}

function dateTimeText(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

module.exports = { price, percent, compactNumber, dateText, dateTimeText }
