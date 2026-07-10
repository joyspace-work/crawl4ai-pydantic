const HOST_MAP: Record<string, string> = {
  "www-shanghai-gov-cn": "www.shanghai.gov.cn",
  "zwdt-sh-gov-cn": "zwdt.sh.gov.cn"
};

interface Env {
  DB: D1Database;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    
    // Extract hostname, handling wrangler dev preview
    let incomingHost = url.hostname;
    const mfOriginalUrl = request.headers.get("mf-original-url");
    if (mfOriginalUrl) {
      try {
        incomingHost = new URL(mfOriginalUrl).hostname;
      } catch {}
    }
    incomingHost = incomingHost.split(":")[0];
    const incomingOrigin = `https://${incomingHost}`;

    let siteKey: string | null = null;
    let isPrefixedRequest = false;

    // 1. Parse prefix from path
    const pathSegments = url.pathname.split("/").filter(Boolean);
    const firstSegment = pathSegments[0];

    if (firstSegment && HOST_MAP[firstSegment]) {
      siteKey = firstSegment;
      isPrefixedRequest = true;
    }
    
    // 2. Intercept sitemap.xml and sitemap-test.xml requests
    const isSitemap = url.pathname === "/sitemap.xml" || 
                      url.pathname === "/sitemap-test.xml" || 
                      (siteKey && (pathSegments[1] === "sitemap.xml" || pathSegments[1] === "sitemap-test.xml"));
    if (isSitemap) {
      return handleSitemap(request, env, siteKey, incomingOrigin);
    }
    
    // 3. Resolve siteKey for unprefixed requests on unified domains
    if (!siteKey) {
      // Check static path prefixes first (highest precedence to avoid cookie contamination)
      const zwdtPathPrefixes = ["/qykj/", "/zwdtSW/", "/govPortals/", "/smzy/", "/uc/"];
      const isZwdt = zwdtPathPrefixes.some(p => url.pathname.startsWith(p));
      if (isZwdt) {
        siteKey = "zwdt-sh-gov-cn";
      } else if (url.pathname.startsWith("/zhengce/")) {
        siteKey = "www-shanghai-gov-cn";
      }

      // Check site_context cookie next
      if (!siteKey) {
        const cookieHeader = request.headers.get("Cookie") || "";
        const contextMatch = cookieHeader.match(/site_context=([^;]+)/);
        if (contextMatch && HOST_MAP[contextMatch[1]]) {
          siteKey = contextMatch[1];
        }
      }
      
      // Fallback: check Referer header
      if (!siteKey) {
        const referer = request.headers.get("Referer");
        if (referer) {
          try {
            const refererUrl = new URL(referer);
            const refererSegments = refererUrl.pathname.split("/").filter(Boolean);
            if (refererSegments[0] && HOST_MAP[refererSegments[0]]) {
              siteKey = refererSegments[0];
            }
          } catch {}
        }
      }
    }
    
    // 4. If no siteKey resolved, check root path or return 404 status dashboard
    if (!siteKey) {
      if (url.pathname === "/") {
        return renderDashboard(incomingOrigin, {
          incomingHost,
          urlHref: url.href,
          headers: Array.from(request.headers.entries())
        });
      }
      return new Response(JSON.stringify({ 
        error: "Site context not resolved.", 
        message: "Please visit a prefixed URL first, e.g. /www-shanghai-gov-cn/",
        debug: {
          incomingHost,
          urlHref: url.href,
          headers: Array.from(request.headers.entries())
        }
      }), {
        status: 404,
        headers: { "content-type": "application/json" }
      });
    }
    
    const targetHost = HOST_MAP[siteKey];
    const targetOrigin = `https://${targetHost}`;
    
    // 5. Construct target URL
    const targetUrl = new URL(url.toString());
    targetUrl.hostname = targetHost;
    
    // Strip the prefix from path if it was a prefixed request
    if (isPrefixedRequest) {
      const newPath = "/" + pathSegments.slice(1).join("/");
      targetUrl.pathname = newPath;
    }
    
    // 7. Setup proxy request headers (including cookie translation)
    const headers = new Headers(request.headers);
    headers.set("Host", targetHost);
    headers.set("Referer", targetOrigin);
    
    // Cookie translation: extract only cookies prefixed with this siteKey
    const originalCookies = request.headers.get("Cookie");
    const translatedCookies = extractPrefixedCookies(originalCookies, siteKey);
    if (translatedCookies) {
      headers.set("Cookie", translatedCookies);
    } else {
      headers.delete("Cookie");
    }
    
    // 8a. For Cloudflare AI Search crawler on zwdt SPA: serve pre-built HTML from D1
    //     zwdt.sh.gov.cn is a React SPA — the crawler cannot execute JS so it gets
    //     empty content. We bypass the upstream fetch and return static HTML instead.
    const crawlerUA = request.headers.get("user-agent") || "";
    if (crawlerUA.includes("Cloudflare-AI-Search") && siteKey === "zwdt-sh-gov-cn" && url.pathname.includes("/project-detail")) {
      const id = url.searchParams.get("id");
      if (id) {
        const doc = await env.DB.prepare(
          "SELECT source_text, policy_region, policy_department, policy_category, application_start_date, application_end_date FROM ai_document WHERE source_url LIKE ? LIMIT 1"
        ).bind(`%id=${id}%`).first<{
          source_text: string | null;
          policy_region: string | null;
          policy_department: string | null;
          policy_category: string | null;
          application_start_date: number | null;
          application_end_date: number | null;
        }>();
        if (doc && doc.source_text) {
          const formatDatetime = (ts: number | null | undefined) => {
            if (!ts) return "";
            try {
              const d = new Date(ts);
              const pad = (n: number) => n.toString().padStart(2, '0');
              return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}+08:00`;
            } catch {
              return "";
            }
          };

          const formatRegion = (reg: string | null | undefined) => {
            if (!reg) return "全国,,全国,,";
            if (reg.includes("区") || reg.includes("县")) return `上海市,,上海市,,${reg}`;
            if (reg === "上海" || reg === "上海市") return "上海市,,上海市,,";
            return `${reg},,${reg},,`;
          };

          const startDate = formatDatetime(doc.application_start_date);
          const endDate   = formatDatetime(doc.application_end_date);
          const regionVal = formatRegion(doc.policy_region);
          const agencyVal = doc.policy_department || "";

          let metaTags = "";
          if (startDate)           metaTags += `  <meta name="startdate" content="${startDate}">\n`;
          if (endDate)             metaTags += `  <meta name="enddate" content="${endDate}">\n`;
          if (regionVal)           metaTags += `  <meta name="region" content="${escapeXml(regionVal)}">\n`;
          if (agencyVal)           metaTags += `  <meta name="agency" content="${escapeXml(agencyVal)}">\n`;
          if (doc.policy_category) metaTags += `  <meta name="category" content="${escapeXml(doc.policy_category)}">\n`;

          // Convert plain-text source_text to simple HTML paragraphs
          const bodyHtml = doc.source_text
            .split(/\n+/)
            .filter(line => line.trim())
            .map(line => `<p>${escapeXml(line.trim())}</p>`)
            .join("\n");

          const staticHtml = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
${metaTags}</head>
<body>
${bodyHtml}
</body>
</html>`;
          return new Response(staticHtml, {
            status: 200,
            headers: { "content-type": "text/html; charset=utf-8" }
          });
        }
      }
    }

    // 8. Fetch from target
    const proxyRequest = new Request(targetUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "manual"
    });
    
    const response = await fetch(proxyRequest);
    
    // 9. Handle redirect rewriting
    if ([301, 302, 307, 308].includes(response.status)) {
      const location = response.headers.get("Location");
      if (location) {
        const newLocation = rewriteUrl(location, incomingOrigin);
        const redirectHeaders = new Headers(response.headers);
        redirectHeaders.set("Location", newLocation);
        
        // Also inject siteKey prefix context cookie
        redirectHeaders.append("Set-Cookie", `site_context=${siteKey}; Path=/; Secure; SameSite=Lax; Max-Age=3600`);
        
        return new Response(null, {
          status: response.status,
          statusText: response.statusText,
          headers: redirectHeaders
        });
      }
    }
    
    // 10. Rewrite response body for HTML/CSS/JS (including absolute URL replacement)
    const contentType = response.headers.get("content-type") || "";
    const isHtml = contentType.includes("text/html");
    const isHtmlOrCssOrJs = isHtml || 
                            contentType.includes("application/javascript") || 
                            contentType.includes("text/css") ||
                            contentType.includes("application/json");
                            
    const newHeaders = new Headers(response.headers);
    
    // Set site context cookie to keep the context active for future unprefixed requests
    newHeaders.append("Set-Cookie", `site_context=${siteKey}; Path=/; Secure; SameSite=Lax; Max-Age=3600`);
    
    // Rename cookies set by target to avoid collision
    const setCookieHeaders = response.headers.getSetCookie();
    if (setCookieHeaders.length > 0) {
      newHeaders.delete("Set-Cookie");
      // Add context cookie
      newHeaders.append("Set-Cookie", `site_context=${siteKey}; Path=/; Secure; SameSite=Lax; Max-Age=3600`);
      // Add renamed target cookies
      for (const setCookieVal of setCookieHeaders) {
        newHeaders.append("Set-Cookie", rewriteSetCookieHeader(setCookieVal, siteKey, incomingHost));
      }
    }
    

    if (isHtmlOrCssOrJs) {
      let bodyText = await response.text();
      
      // Rewrite links and absolute URLs in the body text
      bodyText = rewriteBodyText(bodyText, incomingOrigin);
      
      // Strip all links when Cloudflare AI Search crawler is detected to prevent recursive crawling
      const userAgent = request.headers.get("user-agent") || "";
      if (isHtml && userAgent.includes("Cloudflare-AI-Search")) {
        bodyText = stripHtmlLinks(bodyText);
      }

      // Inject history state rewriter to clean prefix for prefixed routes on client side
      if (isHtml && isPrefixedRequest) {
        bodyText = injectPrefixCleaner(bodyText, siteKey);
      }

      // Fetch metadata from D1 and inject as meta tags for Cloudflare AI Search Crawler
      if (isHtml) {
        try {
          let docResult: any = null;
          if (siteKey === "www-shanghai-gov-cn" && url.pathname.includes("/detail")) {
            const businessId = url.searchParams.get("businessId");
            if (businessId) {
              docResult = await env.DB.prepare(
                "SELECT policy_category, policy_region, policy_department, application_start_date, application_end_date FROM ai_document WHERE source_url LIKE ? LIMIT 1"
              ).bind(`%businessId=${businessId}%`).first();
            }
          } else if (siteKey === "zwdt-sh-gov-cn" && url.pathname.includes("/project-detail")) {
            const id = url.searchParams.get("id");
            if (id) {
              docResult = await env.DB.prepare(
                "SELECT policy_category, policy_region, policy_department, application_start_date, application_end_date FROM ai_document WHERE source_url LIKE ? LIMIT 1"
              ).bind(`%id=${id}%`).first();
            }
          }

          if (docResult) {
            const formatDatetime = (ts: number | null | undefined) => {
              if (!ts) return "";
              try {
                const d = new Date(ts);
                const pad = (n: number) => n.toString().padStart(2, '0');
                return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}+08:00`;
              } catch {
                return "";
              }
            };

            const formatRegion = (reg: string | null | undefined) => {
              if (!reg) return "全国,,全国,,";
              if (reg.includes("区") || reg.includes("县")) return `上海市,,上海市,,${reg}`;
              if (reg === "上海" || reg === "上海市") return "上海市,,上海市,,";
              return `${reg},,${reg},,`;
            };

            const startDateStr = formatDatetime(docResult.application_start_date);
            const endDateStr = formatDatetime(docResult.application_end_date);
            const regionVal = formatRegion(docResult.policy_region);
            const agencyVal = docResult.policy_department || "";

            let injectedMetaTags = "";
            if (startDateStr) injectedMetaTags += `  <meta name="startdate" content="${startDateStr}">\n`;
            if (endDateStr)   injectedMetaTags += `  <meta name="enddate" content="${endDateStr}">\n`;
            if (regionVal)   injectedMetaTags += `  <meta name="region" content="${escapeXml(regionVal)}">\n`;
            if (agencyVal)   injectedMetaTags += `  <meta name="agency" content="${escapeXml(agencyVal)}">\n`;
            if (docResult.policy_category) injectedMetaTags += `  <meta name="category" content="${escapeXml(docResult.policy_category)}">\n`;

            if (injectedMetaTags) {
              bodyText = injectMetaTags(bodyText, injectedMetaTags);
            }
          }
        } catch (err) {
          console.error("Failed to query metadata for tags injection:", err);
        }
      }
      
      newHeaders.delete("content-length");
      return new Response(bodyText, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders
      });
    }
    
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders
    });
  }
};

function injectPrefixCleaner(html: string, siteKey: string): string {
  const script = `<script>
    (function() {
      try {
        const prefix = "/${siteKey}";
        if (window.location.pathname.startsWith(prefix)) {
          const newPath = window.location.pathname.substring(prefix.length) || "/";
          window.history.replaceState(null, "", newPath + window.location.search + window.location.hash);
        }
      } catch (e) {
        console.error("Failed to clean path prefix:", e);
      }
    })();
  </script>`;
  
  if (html.includes("<head>")) {
    return html.replace("<head>", `<head>${script}`);
  }
  return script + html;
}

function rewriteBodyText(text: string, incomingOrigin: string): string {
  let result = text;
  
  for (const [key, host] of Object.entries(HOST_MAP)) {
    result = result
      .replaceAll(`https://${host}`, `${incomingOrigin}/${key}`)
      .replaceAll(`http://${host}`, `${incomingOrigin}/${key}`)
      .replaceAll(`//${host}`, `//${new URL(incomingOrigin).host}/${key}`);
  }
  
  return result;
}

function rewriteUrl(urlStr: string, incomingOrigin: string): string {
  try {
    const u = new URL(urlStr);
    for (const [key, host] of Object.entries(HOST_MAP)) {
      if (u.hostname === host) {
        return urlStr
          .replaceAll(`https://${host}`, `${incomingOrigin}/${key}`)
          .replaceAll(`http://${host}`, `${incomingOrigin}/${key}`);
      }
    }
  } catch {}
  return urlStr;
}

function stripHtmlLinks(html: string): string {
  // Replace href="something" or href='something' with href="#" to prevent link discovery
  return html.replace(/\bhref\s*=\s*(['"])(.*?)\1/gi, 'href="#"');
}

async function handleSitemap(
  request: Request,
  env: Env,
  filterSiteKey: string | null,
  incomingOrigin: string
): Promise<Response> {
  try {
    const reqUrl = new URL(request.url);
    const isTestOnly = reqUrl.searchParams.get("test") === "true" || 
                       reqUrl.pathname.includes("sitemap-test.xml") ||
                       reqUrl.searchParams.get("full") !== "true";
    let results: Array<{ source_url: string; updated_at: number }> = [];

    if (isTestOnly) {
      // Fetch 1 document from www-shanghai-gov-cn
      const shResult = await env.DB.prepare(
        "SELECT source_url, updated_at FROM ai_document WHERE employee_key = 'policy' AND is_active = 1 AND source_url LIKE '%www.shanghai.gov.cn%' LIMIT 1"
      ).first<{ source_url: string; updated_at: number }>();
      if (shResult) results.push(shResult);

      // Fetch 1 document from zwdt-sh-gov-cn
      const zwdtResult = await env.DB.prepare(
        "SELECT source_url, updated_at FROM ai_document WHERE employee_key = 'policy' AND is_active = 1 AND source_url LIKE '%zwdt.sh.gov.cn%' LIMIT 1"
      ).first<{ source_url: string; updated_at: number }>();
      if (zwdtResult) results.push(zwdtResult);
    } else {
      // Query active policy URLs and updated_at timestamps from D1 database
      const dbResults = await env.DB.prepare(
        "SELECT source_url, updated_at FROM ai_document WHERE employee_key = 'policy' AND is_active = 1 LIMIT 50000"
      ).all<{ source_url: string; updated_at: number }>();
      if (dbResults.results) {
        results = dbResults.results;
      }
    }

    type SitemapItem = {
      loc: string;
      lastmod?: string;
      changefreq?: string;
      priority?: string;
    };
    const urls = new Map<string, SitemapItem>();
    
    // Add start pages
    if (!isTestOnly) {
      if (!filterSiteKey || filterSiteKey === "www-shanghai-gov-cn") {
        const path = "/zhengce/more?level=city";
        const startUrl = `${incomingOrigin}/www-shanghai-gov-cn${path}`;
        urls.set(startUrl, {
          loc: startUrl,
          lastmod: new Date().toISOString().split("T")[0],
          changefreq: "daily",
          priority: "1.0",
        });
      }
      if (!filterSiteKey || filterSiteKey === "zwdt-sh-gov-cn") {
        const path = "/qykj/shell_oc_policy_zq/policy/policyDeclare?tab=policyCenter&selectParams=%7B%22policyDeclare%22%3A%7B%22applyState%22%3A%5B%5D,%22isNeedSort%22%3Afalse,%22freeEnjoy%22%3A%22%22,%22regions%22%3A%5B%5D,%22policyType%22%3A%5B%5D,%22hylb%22%3A%5B%5D,%22qylx%22%3A%5B%5D,%22qygm%22%3A%5B%5D,%22publishDepartments%22%3A%5B%5D%7D%7D";
        const startUrl = `${incomingOrigin}/zwdt-sh-gov-cn${path}`;
        urls.set(startUrl, {
          loc: startUrl,
          lastmod: new Date().toISOString().split("T")[0],
          changefreq: "daily",
          priority: "1.0",
        });
      }
    }

    if (results) {
      for (const row of results) {
        if (!row.source_url) continue;
        
        let matchedKey: string | null = null;
        for (const [key, host] of Object.entries(HOST_MAP)) {
          if (row.source_url.includes(host)) {
            matchedKey = key;
            break;
          }
        }
        
        // If filterSiteKey is specified, we only include URLs for that site
        if (filterSiteKey && matchedKey !== filterSiteKey) {
          continue;
        }
        
        if (matchedKey) {
          const host = HOST_MAP[matchedKey];
          const proxiedUrl = row.source_url
            .replaceAll(`https://${host}`, `${incomingOrigin}/${matchedKey}`)
            .replaceAll(`http://${host}`, `${incomingOrigin}/${matchedKey}`);
          
          const lastmodDate = row.updated_at ? new Date(row.updated_at) : new Date();
          const lastmod = isNaN(lastmodDate.getTime()) ? new Date().toISOString().split("T")[0] : lastmodDate.toISOString().split("T")[0];
          const locUrl = proxiedUrl;

          urls.set(proxiedUrl, {
            loc: locUrl,
            lastmod,
            changefreq: "weekly",
            priority: "0.8",
          });
        }
      }
    }

    const sitemapXml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${Array.from(urls.values())
  .map((item) => {
    let xml = `  <url>\n    <loc>${escapeXml(item.loc)}</loc>`;
    if (item.lastmod) xml += `\n    <lastmod>${item.lastmod}</lastmod>`;
    if (item.changefreq) xml += `\n    <changefreq>${item.changefreq}</changefreq>`;
    if (item.priority) xml += `\n    <priority>${item.priority}</priority>`;
    xml += `\n  </url>`;
    return xml;
  })
  .join("\n")}
</urlset>`;
    return new Response(sitemapXml, {
      headers: {
        "content-type": "application/xml; charset=utf-8",
        "cache-control": isTestOnly
          ? "no-store, no-cache, must-revalidate, max-age=0"
          : "public, max-age=3600"
      }
    });
  } catch (error) {
    return new Response(`Failed to generate sitemap: ${error instanceof Error ? error.message : String(error)}`, { status: 500 });
  }
}

function renderDashboard(incomingOrigin: string, debugInfo?: any): Response {
  const debugPanel = debugInfo ? `
    <div class="bg-slate-950 p-6 rounded-xl border border-slate-800/80 mb-6 text-left">
      <h3 class="font-semibold text-rose-400 mb-3 text-sm">Debug Diagnostics</h3>
      <pre class="text-xs text-slate-400 overflow-auto max-h-60 p-3 bg-slate-900 rounded-lg border border-slate-850"><code>${JSON.stringify(debugInfo, null, 2)}</code></pre>
    </div>
  ` : "";

  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>悦空间 (YUE) Policy Crawler Proxy Gateway</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {
      background-color: #0b0f19;
      color: #f3f4f6;
    }
  </style>
</head>
<body class="min-h-screen flex flex-col items-center justify-center p-6">
  <div class="max-w-4xl w-full bg-slate-900/50 backdrop-blur-md border border-slate-800 rounded-2xl p-8 shadow-2xl">
    <div class="flex items-center justify-between mb-8 pb-6 border-b border-slate-800">
      <div>
        <h1 class="text-2xl font-bold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">YUE Policy Crawler Proxy</h1>
        <p class="text-sm text-slate-400 mt-1">悦空间多站点统一反代网关</p>
      </div>
      <span class="px-3 py-1 text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full">Gate Online</span>
    </div>

    <div class="grid gap-6 md:grid-cols-2 mb-8">
      <div class="bg-slate-950 p-6 rounded-xl border border-slate-800/80">
        <h3 class="font-semibold text-blue-400 mb-2">上海官网反代</h3>
        <p class="text-xs text-slate-400 mb-4">目标站点: www.shanghai.gov.cn</p>
        <div class="space-y-2">
          <a href="${incomingOrigin}/www-shanghai-gov-cn/zhengce/more?level=city" target="_blank" class="block w-full text-center py-2 bg-blue-600 hover:bg-blue-500 text-sm font-medium rounded-lg transition-colors">访问反代首页</a>
          <a href="${incomingOrigin}/www-shanghai-gov-cn/sitemap.xml" target="_blank" class="block w-full text-center py-2 bg-slate-800 hover:bg-slate-700 text-sm font-medium rounded-lg transition-colors">查看 Sitemap.xml</a>
        </div>
      </div>

      <div class="bg-slate-950 p-6 rounded-xl border border-slate-800/80">
        <h3 class="font-semibold text-indigo-400 mb-2">随申兑反代</h3>
        <p class="text-xs text-slate-400 mb-4">目标站点: zwdt.sh.gov.cn</p>
        <div class="space-y-2">
          <a href="${incomingOrigin}/zwdt-sh-gov-cn/qykj/shell_oc_policy_zq/policy/policyDeclare?tab=policyCenter" target="_blank" class="block w-full text-center py-2 bg-indigo-600 hover:bg-indigo-500 text-sm font-medium rounded-lg transition-colors">访问反代首页</a>
          <a href="${incomingOrigin}/zwdt-sh-gov-cn/sitemap.xml" target="_blank" class="block w-full text-center py-2 bg-slate-800 hover:bg-slate-700 text-sm font-medium rounded-lg transition-colors">查看 Sitemap.xml</a>
        </div>
      </div>
    </div>

    <div class="bg-slate-950 p-6 rounded-xl border border-slate-800/80 mb-6">
      <h3 class="font-semibold text-slate-300 mb-3">统一 Sitemap 索引 (所有平台)</h3>
      <div class="flex items-center justify-between p-3 bg-slate-900 rounded-lg border border-slate-800">
        <code class="text-xs text-slate-300 select-all">${incomingOrigin}/sitemap.xml</code>
        <a href="${incomingOrigin}/sitemap.xml" target="_blank" class="text-xs text-blue-400 hover:underline">打开 ↗</a>
      </div>
    </div>

    ${debugPanel}

    <div class="text-center text-xs text-slate-500">
      Powered by Cloudflare Workers & D1 Database
    </div>
  </div>
</body>
</html>`;

  return new Response(html, {
    headers: { "content-type": "text/html; charset=utf-8" }
  });
}

function rewriteSetCookieHeader(headerValue: string, siteKey: string, incomingHost: string): string {
  return headerValue.split(',').map(cookieStr => {
    const trimmed = cookieStr.trim();
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) return cookieStr;
    const name = trimmed.substring(0, eqIdx);
    const rest = trimmed.substring(eqIdx);
    
    if (name.startsWith(`${siteKey}_`)) {
      return trimmed;
    }
    
    let newCookie = `${siteKey}_${name}${rest}`;
    newCookie = newCookie.replace(/Domain=[^;]+/gi, `Domain=${incomingHost}`);
    newCookie = newCookie.replace(/Path=[^;]+/gi, `Path=/`);
    return newCookie;
  }).join(', ');
}

function extractPrefixedCookies(cookieHeader: string | null, siteKey: string): string {
  if (!cookieHeader) return "";
  return cookieHeader.split(';')
    .map(c => c.trim())
    .filter(c => c.startsWith(`${siteKey}_`))
    .map(c => {
      const parts = c.split('=');
      const name = parts[0].substring(siteKey.length + 1);
      const val = parts.slice(1).join('=');
      return `${name}=${val}`;
    })
    .join('; ');
}

function escapeXml(unsafe: string): string {
  return unsafe.replace(/[<>&'"]/g, (c) => {
    switch (c) {
      case "<": return "&lt;";
      case ">": return "&gt;";
      case "&": return "&amp;";
      case "'": return "&apos;";
      case "\"": return "&quot;";
      default: return c;
    }
  });
}

function injectMetaTags(html: string, metaTags: string): string {
  if (html.includes("<head>")) {
    return html.replace("<head>", `<head>\n${metaTags}`);
  }
  return metaTags + html;
}

