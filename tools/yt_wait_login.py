#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等用户在 Chrome 里登录 Google，检测到登录 cookie 后自动导出 cookies.txt 并下载指定 YouTube 音频"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = 9333
LOGIN_KEYS = {"SID", "HSID", "SSID", "APISID", "SAPISID",
              "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO", "__Secure-3PAPISID"}
OUT_COOKIES = r"D:\DSHgongzuoqualiyun\cover\win\yt_cookies.txt"
DL_DIR = r"C:\Users\Administrator\Downloads\harmony_exp"
PY = r"D:\DSHgongzuoqualiyun\cover\win\venv\Scripts\python.exe"


def jget(url):
    return json.load(urllib.request.urlopen(url, timeout=5))


def get_cookies():
    from websockets.sync.client import connect
    ts = jget(f"http://127.0.0.1:{PORT}/json")
    pg = [t for t in ts if t.get("type") == "page" and "youtube" in (t.get("url") or "")]
    if not pg:
        pg = [t for t in ts if t.get("type") == "page"]
    if not pg:
        return []
    with connect(pg[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024, open_timeout=15) as ws:
        ws.send(json.dumps({"id": 1, "method": "Network.getCookies",
                            "params": {"urls": ["https://www.youtube.com/", "https://www.google.com/"]}}))
        for _ in range(40):
            m = json.loads(ws.recv(timeout=20))
            if m.get("id") == 1:
                return m["result"]["cookies"]
    return []


def write_netscape(cs):
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cs:
        if "youtube" not in c["domain"] and "google" not in c["domain"]:
            continue
        exp = int(c.get("expires") or 0) or int(time.time()) + 31536000
        lines.append("\t".join([c["domain"], "TRUE" if c["domain"].startswith(".") else "FALSE",
                                c.get("path") or "/", "TRUE" if c.get("secure") else "FALSE",
                                str(exp), c["name"], c["value"]]))
    os.makedirs(os.path.dirname(OUT_COOKIES), exist_ok=True)
    with open(OUT_COOKIES, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return OUT_COOKIES


def download(url, name):
    cmd = [PY, "-m", "yt_dlp", "--cookies", OUT_COOKIES, "--js-runtimes", "node",
           "-x", "--audio-format", "wav", "--audio-quality", "0", "--no-playlist",
           "-o", os.path.join(DL_DIR, name + ".%(ext)s"), url]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = p.returncode == 0
    print(f"[dl] {name} {'OK' if ok else 'FAIL: ' + (p.stderr or '')[-400:]}", flush=True)
    return ok


def main():
    print("等待登录…（检测 SID/__Secure-3PSID）", flush=True)
    for i in range(240):
        try:
            cs = get_cookies()
        except Exception as e:  # noqa: BLE001
            cs = []
            if i % 6 == 0:
                print(f"[{i*10}s] CDP 重连中 {type(e).__name__}", flush=True)
        names = {c["name"] for c in cs}
        hit = LOGIN_KEYS & names
        if hit:
            print(f"检测到登录 cookie: {sorted(hit)}", flush=True)
            path = write_netscape(cs)
            print(f"已写 {path}", flush=True)
            ok1 = download("https://www.youtube.com/watch?v=VPP8012DBsw", "yt_vocal")
            ok2 = download("https://www.youtube.com/watch?v=C8cwAL391QU", "yt_inst")
            print("RESULT", ok1, ok2, flush=True)
            return
        if i % 6 == 0:
            print(f"[{i*10}s] 还没登录（cookies {len(cs)} 条）", flush=True)
        time.sleep(10)
    print("超时未登录", flush=True)


if __name__ == "__main__":
    main()
