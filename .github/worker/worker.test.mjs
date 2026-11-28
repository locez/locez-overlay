import assert from "node:assert/strict";
import crypto from "node:crypto";
import { test } from "node:test";
import worker, { md5Hex, signWpsUrl } from "./worker.js";

test("md5Hex matches node:crypto md5 on standard and WPS inputs", () => {
  const vectors = [
    "",
    "a",
    "abc",
    "message digest",
    "7f8faaaa468174dc1c9cd62e5f218a5b/wps/download/ep/Linux2023/29183/wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb1795854806",
  ];
  for (const s of vectors) {
    const expected = crypto.createHash("md5").update(s).digest("hex");
    assert.equal(md5Hex(s), expected);
  }
});

test("signWpsUrl produces exact deterministic timestamped URL", () => {
  const signed = signWpsUrl(
    "/wps/download/ep/Linux2023/29183/wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb",
    1795854806
  );
  assert.equal(
    signed,
    "https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/29183/wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb?t=1795854806&k=75aa665471fa885e34931750f1fcb839"
  );
});

test("health endpoints return 200 and non-GET/HEAD return 405", async () => {
  for (const path of ["/", "/healthz"]) {
    const getResp = await worker.fetch(new Request(`https://overlay.locez.com${path}`));
    assert.equal(getResp.status, 200);
    const headResp = await worker.fetch(
      new Request(`https://overlay.locez.com${path}`, { method: "HEAD" })
    );
    assert.equal(headResp.status, 200);
  }
  const postResp = await worker.fetch(
    new Request("https://overlay.locez.com/", { method: "POST" })
  );
  assert.equal(postResp.status, 405);
});

test("WPS signing returns 302 with no-store and tolerates collapsed https:/", async () => {
  const urls = [
    "https://overlay.locez.com/https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/29183/wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb",
    "https://overlay.locez.com/https:/wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/29183/wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb",
  ];
  for (const u of urls) {
    const resp = await worker.fetch(new Request(u, { method: "HEAD" }));
    assert.equal(resp.status, 302);
    assert.equal(resp.headers.get("Cache-Control"), "no-store");
    assert.match(
      resp.headers.get("Location"),
      /^https:\/\/wps-linux-personal\.wpscdn\.cn\/wps\/download\/ep\/Linux2023\/29183\/wps-office_.*\.deb\?t=\d+&k=[0-9a-f]{32}$/
    );
  }
});

test("QQ signing calls UrlSign RPC and returns 302 (mocked)", async () => {
  const origFetch = globalThis.fetch;
  let capturedInit;
  globalThis.fetch = async (url, init) => {
    capturedInit = { url, init };
    return new Response(
      JSON.stringify({
        retcode: 0,
        data: {
          url: "https://qqdl.gtimg.cn/qqfile/QQNTV2/test/QQ_1.0_amd64_01.deb?sign=deadbeef&t=1800000000",
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  };
  try {
    const resp = await worker.fetch(
      new Request("https://overlay.locez.com/https://qqdl.gtimg.cn/qqfile/QQNTV2/test/QQ_1.0_amd64_01.deb")
    );
    assert.equal(resp.status, 302);
    assert.equal(resp.headers.get("Cache-Control"), "no-store");
    assert.equal(
      resp.headers.get("Location"),
      "https://qqdl.gtimg.cn/qqfile/QQNTV2/test/QQ_1.0_amd64_01.deb?sign=deadbeef&t=1800000000"
    );
    assert.equal(capturedInit.init.method, "POST");
  } finally {
    globalThis.fetch = origFetch;
  }
});

test("whitelist rejects foreign hosts, wrong paths, traversal, and query params", async () => {
  const disallowed = [
    "https://overlay.locez.com/https://evil.example.com/foo.deb",
    "https://overlay.locez.com/http://qqdl.gtimg.cn/qqfile/QQNTV2/a/QQ.deb",
    "https://overlay.locez.com/https://qqdl.gtimg.cn/other/foo.deb",
    "https://overlay.locez.com/https://qqdl.gtimg.cn/qqfile/QQNTV2/../../etc/passwd.deb",
    "https://overlay.locez.com/https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/foo.exe",
    "https://overlay.locez.com/https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/foo.deb?x=1",
  ];
  for (const bad of disallowed) {
    const resp = await worker.fetch(new Request(bad));
    assert.equal(resp.status, 403, `Expected 403 for ${bad}`);
  }
});
