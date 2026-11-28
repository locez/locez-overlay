# `locez-overlay` 302 Signing Redirector

Minimal, zero-dependency Cloudflare Worker backing `https://overlay.locez.com`.
It **never proxies binary downloads** — it only signs and returns `302 Found`
pointing directly to official upstream CDNs:

- `https://qqdl.gtimg.cn/qqfile/QQNTV2/.../*.{deb,rpm,AppImage}` (via Tencent's official `UrlSign/GetSign` RPC)
- `https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/.../*.deb` (via timestamped MD5 token)

All other hosts/paths return `403 Forbidden`.

## Deploy

### Option A: Cloudflare Dashboard Git Integration (recommended)
1. Cloudflare Dashboard -> **Workers & Pages** -> **Create** -> **Workers** -> **Import a repository**.
2. Select `locez/locez-overlay`.
3. Set **Root directory** to `.github/worker` and **Deploy command** to `npx wrangler deploy`.
4. Push to `master` auto-deploys and binds `overlay.locez.com` from `wrangler.toml`.

### Option B: Manual CLI
```bash
cd .github/worker
npx wrangler deploy
```
