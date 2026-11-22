"""Temporary probe: does a TikTok session cookie work from GitHub Actions?
Prints only counts, statuses and hosts. Never prints cookie values."""
import asyncio, json, os, urllib.parse
from playwright.async_api import async_playwright

QUERY = os.environ.get("PROBE_QUERY", "aurora boreal")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def parse_cookies():
    raw = os.environ.get("TIKTOK_COOKIE", "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    out = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k:
            out.append({"name": k, "value": v, "domain": ".tiktok.com", "path": "/",
                        "secure": True, "sameSite": "None"})
    return out

async def main():
    cookies = parse_cookies()
    names = {c["name"] for c in cookies}
    print(json.dumps({"cookie_count": len(cookies),
                      "has_sessionid": "sessionid" in names,
                      "has_sid_tt": "sid_tt" in names,
                      "has_tt_chain_token": "tt_chain_token" in names}))
    res = {"search_api_hits": 0, "http_status": [], "status_codes": [], "items": 0,
           "sample_ids": []}
    play_urls = []
    captured_items = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(user_agent=UA, locale="es-CL",
                                        viewport={"width": 1366, "height": 900})
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        async def on_resp(r):
            if "/api/search/" not in r.url:
                return
            res["search_api_hits"] += 1
            res["http_status"].append(r.status)
            try:
                j = await r.json()
            except Exception:
                return
            res["status_codes"].append(j.get("status_code"))
            items = j.get("item_list") or j.get("data") or []
            for it in items:
                item = it.get("item", it) if isinstance(it, dict) else {}
                if not isinstance(item, dict):
                    continue
                res["items"] += 1
            captured_items.append(item)
                if item.get("id") and len(res["sample_ids"]) < 5:
                    res["sample_ids"].append(item["id"])
                v = item.get("video") or {}
                u = v.get("playAddr") or v.get("downloadAddr")
                if u:
                    play_urls.append(u)

        page.on("response", on_resp)
        await page.goto("https://www.tiktok.com/search/video?q=" + urllib.parse.quote(QUERY),
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(15000)
        for _ in range(3):
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(3000)
        html = (await page.content()).lower()
        res["captcha_in_page"] = "captcha" in html
        res["login_modal_in_page"] = "login-modal" in html or "logincontainer" in html
        parsed = urllib.parse.urlparse(page.url)
        res["final_path"] = parsed.netloc + parsed.path
        links = await page.eval_on_selector_all('a[href*="/video/"]',
                                                "els => els.map(e => e.href)")
        res["dom_video_links"] = len(set(links))

        if play_urls:
            r = await ctx.request.get(play_urls[0], headers={
                "Referer": "https://www.tiktok.com/", "Range": "bytes=0-1048575"},
                timeout=60000)
            body = await r.body()
            res["media_probe"] = {"host": urllib.parse.urlparse(play_urls[0]).netloc,
                                  "status": r.status,
                                  "content_type": r.headers.get("content-type"),
                                  "bytes": len(body)}
        # Independent nonpersisting probe of the current MediaRef path.
        if captured_items:
            from clipoteca.providers.tiktok import TikTok
            from clipoteca.security.download import download_media
            from pathlib import Path
            import tempfile
            provider = TikTok(cookie=os.environ.get("TIKTOK_COOKIE",""))
            candidate = provider._candidate_from_item(captured_items[0], QUERY, "es", set())
            media = await provider.direct_media(candidate) if candidate else None
            res["current_media_ref"] = "available" if media else "missing"
            if media:
                try:
                    with tempfile.TemporaryDirectory() as folder:
                        target = await download_media(
                            media.url, Path(folder), allowed_hosts=media.allowed_hosts,
                            max_bytes=1048576, probe_prefix_bytes=1048576,
                            total_seconds=35, user_agent=media.user_agent
                        )
                        res["current_media_download"] = "ok" if target.stat().st_size > 12 else "empty"
                except Exception as exc:
                    res["current_media_download"] = type(exc).__name__
            await provider.client.aclose()
        await browser.close()
    print(json.dumps(res, indent=1))

asyncio.run(main())
