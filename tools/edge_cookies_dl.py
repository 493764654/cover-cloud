#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Edge（或任意 Chromium）经 CDP 导出 cookies.txt，并下载指定 YouTube 音频"""
import json
import os
import subprocess
import sys
import time
import urllib.request

EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EXE):
    EXE = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
USER_DATA = r"C:\Users\Administrator\AppData\Local\Microsoft\Edge\User Data"
PORT = 9223
OUT = r"D:\DSHgongzuoqualiyun\cover\win\yt_cookies.txt"
DL = r"C:\Users\Administrator\Downloads\harmony_exp"
PY = sys.executable
LOGIN = {"SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO"}


def jget(u):
    return json.load(urllib.request.urlopen(u, timeout=5))


def kill():
    subprocess.run(["taskkill", "/IM", os.path.basename(EXE), "/F"], capture_output=True)
    time.sleep(2)


def start():
    subprocess.Popen([EXE, f"--user-data-dir={USER_DATA}", "--profile-directory=Default",
                      f"--remote-debugging-port={PORT}", "--no-first-run",
                      "--no-default-browser-check", "https://www.youtube.com/"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            jget(f"http://127.0.0.1:{PORT}/json/version")
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise SystemExit("Edge 调试端口没起来")


def cookies():
    from websockets.sync.client import connect
    ts = jget(f"http://127.0.0.1:{PORT}/json")
    pg = [t for t in ts if t.get("type") == "page" and "youtube" in (t.get("url") or "")]
    if not pg:
        pg = [t for t in ts if t.get("type") == "page"]
    with connect(pg[0]["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024, open_timeout=15) as ws:
        ws.send(json.dumps({"id": 1, "method": "Network.getCookies",
                            "params": {"urls": ["https://www.youtube.com/", "https://www.google.com/"]}}))
        for _ in range(40):
            m = json.loads(ws.recv(timeout=20))
            if m.get("id") == 1:
                return m["result"]["cookies"]
    return []


def write(cs):
    lines = ["# Netscape HTTP Cookie File", ""]
    n = 0
    for c in cs:
        if "youtube" not in c["domain"] and "google" not in c["domain"]:
            continue
        exp = int(c.get("expires") or 0) or int(time.time()) + 31536000
        lines.append("\t".join([c["domain"], "TRUE" if c["domain"].startswith(".") else "FALSE",
                                c.get("path") or "/", "TRUE" if c.get("secure") else "FALSE",
                                str(exp), c["name"], c["value"]]))
        n += 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    return n


def main():
    print("[1] 关闭 Edge"); kill()
    print("[2] 带调试端口启动 Edge"); start()
    time.sleep(5)
    cs = cookies()
    names = {c["name"] for c in cs}
    hit = LOGIN & names
    n = write(cs)
    print(f"cookies {len(cs)} 条（写 {n} 条），登录态: {sorted(hit) or '无'}")
    if not hit:
        print("没检测到登录 cookie；保持 Edge 打开供继续登录，10 秒后重试一次")
        for i in range(12):
            time.sleep(10)
            cs = cookies()
            names = {c["name"] for c in cs}
            hit = LOGIN & names
            if hit:
                n = write(cs)
                print(f"登录态出现: {sorted(hit)}（写 {n} 条）")
                break
            print(f"  [{i*10}s] 仍未登录")
    if not hit:
        print("RESULT 未登录")
        return
    for url, name in (("https://www.youtube.com/watch?v=VPP8012DBsw", "yt_vocal"),
                      ("https://www.youtube.com/watch?v=C8cwAL391QU", "yt_inst")):
        cmd = [PY, "-m", "yt_dlp", "--cookies", OUT, "--js-runtimes", "node", "-x",
               "--audio-format", "wav", "--audio-quality", "0", "--no-playlist",
               "-o", os.path.join(DL, name + ".%(ext)s"), url]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"[dl] {name} {'OK' if p.returncode == 0 else 'FAIL ' + (p.stderr or '')[-300:]}", flush=True)
    print("RESULT done")


if __name__ == "__main__":
    main()
