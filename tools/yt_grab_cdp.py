#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实 Chrome 播放 YouTube，从 CDP 抓 googlevideo 音频流 URL（不需要 cookies）

用法：python yt_grab_cdp.py <watch_url> [输出json]
依赖：本机 Chrome 已用 --remote-debugging-port=9333 启动（tools/yt_cookies_cdp.py --keep 会开）
"""
import json
import sys
import time
import urllib.request

PORT = 9333
VIDEO_ID = ""


def pages():
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))


def main() -> None:
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "grab.json"
    vid = url.split("v=")[1].split("&")[0] if "v=" in url else ""

    from websockets.sync.client import connect

    target = [t for t in pages() if t.get("type") == "page" and "chrome://" not in (t.get("url") or "")]
    if not target:
        raise SystemExit("没有可用 page target")
    ws_url = target[0]["webSocketDebuggerUrl"]
    print("attach", ws_url)

    found: list[dict] = []
    with connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=15) as ws:
        def send(i, method, params=None):
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))

        send(1, "Network.enable")
        send(2, "Page.enable")
        send(3, "Page.navigate", {"url": url})
        time.sleep(4)
        # 触发播放（静音自动播放允许）
        send(4, "Runtime.evaluate", {"expression":
             "(()=>{const v=document.querySelector('video');if(!v)return 'no-video';"
             "v.muted=true;v.play().catch(()=>{});return 'playing '+v.duration;})()"})

        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv(timeout=5))
            except Exception:  # noqa: BLE001
                continue
            m = msg.get("method")
            if m == "Network.requestWillBeSent":
                req = msg["params"]["request"]
                u = req.get("url") or ""
                if "googlevideo.com/videoplayback" in u and "mime=audio" in u:
                    found.append({"url": u, "headers": req.get("headers", {})})
                    print("AUDIO", u[:160])
                    if len(found) >= 2:
                        break
            elif m == "Network.responseReceived":
                r = msg["params"]["response"]
                if "googlevideo.com/videoplayback" in (r.get("url") or ""):
                    ct = r.get("mimeType", "")
                    if "audio" in ct:
                        print("RESP", ct, r["url"][:120])

    json.dump(found, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"抓到 {len(found)} 条音频请求 -> {out}")


if __name__ == "__main__":
    main()
