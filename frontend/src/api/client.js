// 统一请求入口：解析后端 {code, message, data} 信封，code !== 0 一律抛 Error
async function request(path, options = {}) {
  let resp
  try {
    resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    // fetch 只有网络层失败才 reject（4xx/5xx 都算"到达了"）
    throw new Error('无法连接后端服务')
  }
  let body
  try {
    body = await resp.json()
  } catch {
    throw new Error('后端返回的不是 JSON（服务可能正在重启）')
  }
  if (body.code !== 0) {
    throw new Error(body.message || `请求失败（code ${body.code}）`)
  }
  return body.data
}

export { request }
