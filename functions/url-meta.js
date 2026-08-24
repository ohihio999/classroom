// tool-manager：網址 metadata 抓取。抽成獨立模組是為了能在部署前本機測試。

const META_TIMEOUT_MS = 8000;
const META_MAX_BYTES = 200 * 1024;         // 只讀開頭，<head> 不會在 200KB 之後
const META_CONCURRENCY = 5;

// 擋掉內網位址：這支會去抓使用者給的任何網址，不擋的話等於開了一個 SSRF 跳板
function isBlockedHost(hostname) {
  const host = hostname.toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.internal')) return true;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    const [a, b] = host.split('.').map(Number);
    if (a === 10 || a === 127 || a === 0) return true;
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 169 && b === 254) return true;        // link-local，雲端 metadata 就在這
  }
  if (host === '[::1]' || host.startsWith('[fc') || host.startsWith('[fd')) return true;
  return false;
}

function decodeEntities(text) {
  return text
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/\s+/g, ' ')
    .trim();
}

function extractMeta(html) {
  const pick = (regexes) => {
    for (const regex of regexes) {
      const match = html.match(regex);
      if (match && match[1] && match[1].trim()) return decodeEntities(match[1]).slice(0, 300);
    }
    return '';
  };

  // og: 通常比 <title> 乾淨（沒有「- 首頁 | 品牌名」那些尾巴），所以排前面
  const title = pick([
    /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i,
    /<title[^>]*>([\s\S]*?)<\/title>/i,
  ]);

  const description = pick([
    /<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]+name=["']description["']/i,
    /<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:description["']/i,
  ]);

  const siteName = pick([
    /<meta[^>]+property=["']og:site_name["'][^>]+content=["']([^"']+)["']/i,
  ]);

  return { title, description, siteName };
}

async function fetchOneMeta(rawUrl) {
  let target;
  try {
    target = new URL(/^https?:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`);
  } catch {
    return { url: rawUrl, ok: false, error: '網址格式不正確' };
  }

  if (!/^https?:$/.test(target.protocol)) return { url: rawUrl, ok: false, error: '只支援 http/https' };
  if (isBlockedHost(target.hostname)) return { url: rawUrl, ok: false, error: '不允許抓取內網位址' };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), META_TIMEOUT_MS);

  try {
    const response = await fetch(target.toString(), {
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        // 不少站對沒有 UA 的請求直接回 403
        'User-Agent': 'Mozilla/5.0 (compatible; ToolManagerBot/1.0)',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
      },
    });

    if (!response.ok) {
      return { url: rawUrl, ok: false, status: response.status, error: `HTTP ${response.status}` };
    }

    const contentType = response.headers.get('content-type') || '';
    if (contentType && !/text\/html|application\/xhtml|text\/plain/i.test(contentType)) {
      return { url: rawUrl, ok: false, error: `不是網頁（${contentType.split(';')[0]}）` };
    }

    // 邊讀邊截：有些站首頁好幾 MB，整份讀進來只是浪費時間跟記憶體
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    while (received < META_MAX_BYTES) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
    }
    reader.cancel().catch(() => {});

    const buffer = Buffer.concat(chunks.map(c => Buffer.from(c)));
    const html = buffer.toString('utf8');
    const meta = extractMeta(html);

    return {
      url: rawUrl,
      ok: true,
      finalUrl: response.url || target.toString(),
      title: meta.title,
      description: meta.description,
      siteName: meta.siteName,
    };
  } catch (err) {
    const reason = err.name === 'AbortError' ? '逾時' : (err.message || '抓取失敗');
    return { url: rawUrl, ok: false, error: reason };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { fetchOneMeta, extractMeta, isBlockedHost, decodeEntities, META_CONCURRENCY };
