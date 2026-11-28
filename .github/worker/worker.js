/**
 * locez-overlay 302 signing redirector for Cloudflare Workers.
 *
 * Strictly signs and 302-redirects whitelisted official upstream installer URLs
 * without proxying any binary traffic:
 *   - Tencent QQ Linux (qqdl.gtimg.cn) via Tencent's official UrlSign RPC
 *   - WPS Office Linux Personal (wps-linux-personal.wpscdn.cn) via timestamp MD5
 */

const QQ_SIGN_API =
  "https://im.qq.com/http2rpc/gotrpc/noauth/trpc.qqntv2.urlsign.UrlSign/GetSign";
const QQ_ALLOWED_PATH = /^\/qqfile\/QQNTV2\/[A-Za-z0-9._/-]+\.(?:deb|rpm|AppImage)$/;

const WPS_SECURITY_KEY = "7f8faaaa468174dc1c9cd62e5f218a5b";
const WPS_ALLOWED_PATH = /^\/wps\/download\/ep\/Linux2023\/[A-Za-z0-9._/-]+\.deb$/;

const MD5_S = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];
const MD5_K = Array.from({ length: 64 }, (_, i) =>
  Math.floor(Math.abs(Math.sin(i + 1)) * 0x100000000) >>> 0
);

export function md5Hex(input) {
  const bytes = new TextEncoder().encode(input);
  const bitLen = bytes.length * 8;
  const paddedLen = (((bytes.length + 8) >> 6) + 1) << 6;
  const buf = new Uint8Array(paddedLen);
  buf.set(bytes);
  buf[bytes.length] = 0x80;

  const view = new DataView(buf.buffer);
  view.setUint32(paddedLen - 8, bitLen >>> 0, true);
  view.setUint32(paddedLen - 4, Math.floor(bitLen / 0x100000000) >>> 0, true);

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  const M = new Uint32Array(16);
  for (let offset = 0; offset < paddedLen; offset += 64) {
    for (let j = 0; j < 16; j++) {
      M[j] = view.getUint32(offset + j * 4, true);
    }
    let a = a0, b = b0, c = c0, d = d0;
    for (let i = 0; i < 64; i++) {
      let f, g;
      if (i < 16) {
        f = (b & c) | (~b & d);
        g = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        g = (5 * i + 1) % 16;
      } else if (i < 48) {
        f = b ^ c ^ d;
        g = (3 * i + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        g = (7 * i) % 16;
      }
      const sum = (a + f + MD5_K[i] + M[g]) >>> 0;
      const rot = ((sum << MD5_S[i]) | (sum >>> (32 - MD5_S[i]))) >>> 0;
      a = d;
      d = c;
      c = b;
      b = (b + rot) >>> 0;
    }
    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }

  return [a0, b0, c0, d0]
    .map((word) => {
      let hex = "";
      for (let i = 0; i < 4; i++) {
        hex += ((word >>> (i * 8)) & 0xff).toString(16).padStart(2, "0");
      }
      return hex;
    })
    .join("");
}

export function signWpsUrl(pathname, nowSec = Math.floor(Date.now() / 1000)) {
  const t = String(nowSec);
  const k = md5Hex(WPS_SECURITY_KEY + pathname + t);
  return `https://wps-linux-personal.wpscdn.cn${pathname}?t=${t}&k=${k}`;
}

async function signQqUrl(upstreamUrl) {
  const resp = await fetch(QQ_SIGN_API, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Oidb": JSON.stringify({ uint32_command: "0x9b8e", uint32_service_type: 1 }),
      Origin: "https://im.qq.com",
      Referer: "https://im.qq.com/",
    },
    body: JSON.stringify({ url: upstreamUrl }),
  });
  if (!resp.ok) {
    throw new Error(`QQ UrlSign API returned HTTP ${resp.status}`);
  }
  const payload = await resp.json();
  const signed = payload?.data?.url;
  if (typeof signed !== "string" || !signed.startsWith("https://qqdl.gtimg.cn/")) {
    throw new Error("QQ UrlSign API returned an unexpected payload");
  }
  return signed;
}

function parseTargetUrl(rawPath) {
  const normalized = rawPath.replace(/^\/+/, "").replace(/^https:\/(?!\/)/, "https://");
  let target;
  try {
    target = new URL(normalized);
  } catch {
    return null;
  }
  if (target.protocol !== "https:" || target.search || target.hash) {
    return null;
  }
  if (target.pathname.includes("..") || target.pathname.includes("\\")) {
    return null;
  }
  return target;
}

function redirectResponse(location) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: location,
      "Cache-Control": "no-store",
    },
  });
}

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed\n", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    const reqUrl = new URL(request.url);
    if (reqUrl.pathname === "/" || reqUrl.pathname === "/healthz") {
      return new Response(
        "locez-overlay 302 signing redirector\n" +
          "Allowed hosts: qqdl.gtimg.cn, wps-linux-personal.wpscdn.cn\n",
        { status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" } }
      );
    }

    if (reqUrl.search) {
      return new Response("Forbidden\n", { status: 403 });
    }

    const target = parseTargetUrl(reqUrl.pathname);
    if (!target) {
      return new Response("Forbidden\n", { status: 403 });
    }

    if (target.hostname === "qqdl.gtimg.cn" && QQ_ALLOWED_PATH.test(target.pathname)) {
      try {
        const signed = await signQqUrl(`${target.origin}${target.pathname}`);
        return redirectResponse(signed);
      } catch (err) {
        return new Response(`Upstream signing error: ${err.message}\n`, { status: 502 });
      }
    }

    if (
      target.hostname === "wps-linux-personal.wpscdn.cn" &&
      WPS_ALLOWED_PATH.test(target.pathname)
    ) {
      return redirectResponse(signWpsUrl(target.pathname));
    }

    return new Response("Forbidden\n", { status: 403 });
  },
};
