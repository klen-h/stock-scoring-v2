/**
 * Cloudflare Worker - 腾讯行情 CORS 代理
 * 
 * 部署步骤：
 * 1. 登录 https://dash.cloudflare.com
 * 2. Workers & Pages → Create Application → Create Worker
 * 3. 粘贴此代码 → Deploy
 * 4. 复制 Worker URL（如 https://tencent-proxy.xxx.workers.dev/）
 * 5. 在 tencent.js 中设置 CF_PROXY_URL
 */

export default {
  async fetch(request) {
    // CORS 预检
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, OPTIONS',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Max-Age': '86400',
        },
      })
    }

    const clientUrl = new URL(request.url)
    
    // 方式1：?url= 参数（带完整目标 URL）
    let targetUrl = clientUrl.searchParams.get('url')
    
    // 方式2：直接转发路径（/q=xxx → qt.gtimg.cn/q=xxx）
    if (!targetUrl) {
      const path = clientUrl.pathname + clientUrl.search
      targetUrl = 'https://qt.gtimg.cn' + path
    }

    // 安全检查：只允许代理腾讯行情
    if (!targetUrl.startsWith('https://qt.gtimg.cn/')) {
      return new Response('Forbidden', { status: 403 })
    }

    try {
      const response = await fetch(targetUrl, {
        method: 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0',
          'Referer': 'https://stockapp.finance.qq.com',
        },
      })

      const corsHeaders = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Cache-Control': 'public, max-age=3',
      }

      return new Response(response.body, {
        status: response.status,
        headers: corsHeaders,
      })
    } catch (err) {
      return new Response('Proxy Error: ' + err.message, { 
        status: 502,
        headers: { 'Access-Control-Allow-Origin': '*' },
      })
    }
  },
}
