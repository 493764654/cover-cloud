#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站音频下载（API + 重试，绕开网页 412）

用法：python bili_dl.py BV1xxx out_inst.wav [BV2 out2.wav ...]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
COOKIE = ""


def http(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        **({"Cookie": COOKIE} if COOKIE else {})})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def api(url: str, tries: int = 6) -> dict:
    last = None
    for i in range(tries):
        try:
            return json.loads(http(url))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 + i * 2)
    raise SystemExit(f"接口失败: {last}")


def bvid_of(s: str) -> str:
    s = s.strip()
    if "BV" in s:
        i = s.index("BV")
        return s[i:i + 12]
    return s


def init_cookie():
    global COOKIE
    try:
        req = urllib.request.Request("https://www.bilibili.com/", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.headers.get_all("Set-Cookie") or []
        jar = {}
        for c in raw:
            kv = c.split(";")[0]
            if "=" in kv:
                k, v = kv.split("=", 1)
                jar[k.strip()] = v.strip()
        COOKIE = "; ".join(f"{k}={v}" for k, v in jar.items())
        print("[bili] cookie:", list(jar))
    except Exception as e:  # noqa: BLE001
        print("[bili] 拿 cookie 失败", type(e).__name__)


def fetch(bv: str, dst: str) -> str:
    v = api(f"https://api.bilibili.com/x/web-interface/view?bvid={bv}")
    if v.get("code") != 0:
        raise SystemExit(f"{bv} view 失败: {v.get('code')} {v.get('message')}")
    d = v["data"]
    cid, title, dur = d["cid"], d["title"], d["duration"]
    print(f"[bili] {bv} | {title} | {dur}s")
    pu = api(f"https://api.bilibili.com/x/player/playurl?bvid={bv}&cid={cid}&fnval=16&fourk=1&qn=0")
    if pu.get("code") != 0:
        raise SystemExit(f"{bv} playurl 失败: {pu.get('code')} {pu.get('message')}")
    js = pu["data"]
    audios = (js.get("dash") or {}).get("audio") or []
    if audios:
        audios.sort(key=lambda a: a.get("bandwidth", 0))
        a = audios[-1]
        url = a["baseUrl"]
        print(f"       audio bw={a['bandwidth']} {a.get('mimeType')}")
    else:
        url = (js.get("durl") or [{}])[0].get("url")
        if not url:
            raise SystemExit("没有音频流（可能需要登录/大会员）")
    raw = http(url, binary=True)
    tmp = dst + ".m4s"
    open(tmp, "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-vn",
                    "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", dst], check=True)
    os.remove(tmp)
    print(f"       -> {dst} {os.path.getsize(dst)/1048576:.1f}MB")
    return dst


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit(__doc__)
    init_cookie()
    for i in range(0, len(args) - 1, 2):
        fetch(bvid_of(args[i]), args[i + 1])
    print("ALLOK")
