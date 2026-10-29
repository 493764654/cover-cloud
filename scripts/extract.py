#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL → wav → R2 inbox/（YouTube / B 站 / 网易云 都走 yt-dlp）

用法：
  python scripts/extract.py --url "https://www.youtube.com/watch?v=xxx" --name nuer1
  python scripts/extract.py --url "..." --name nuer1 --out-dir /tmp/ex --no-upload
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import log, probe, r2_put  # noqa: E402


def slug(s: str, limit: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return s[:limit] or "audio"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", default="", help="R2 里的文件名（不含扩展名）")
    ap.add_argument("--out-dir", default=os.environ.get("WORK", "/tmp/extract"))
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    name = slug(a.name) if a.name else ""
    tmpl = os.path.join(a.out_dir, (name or "dl") + ".%(ext)s")
    cookies = os.environ.get("YT_COOKIES_FILE", "")
    extra = ["--cookies", cookies] if cookies and os.path.exists(cookies) else []
    cmd = [sys.executable, "-m", "yt_dlp", *extra, "-x", "--audio-format", "wav",
           "--audio-quality", "0", "--no-playlist",
           "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
           "--extractor-args", "youtube:player_client=web,default",
           "-o", tmpl, a.url]
    log("download", a.url)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        diag = [l for l in (p.stdout or "").splitlines()
                if re.search(r"plugin|pot|bgutil|PO Token|client|Extractor", l, re.I)][:25]
        raise SystemExit("yt-dlp 失败:\n" + "\n".join(diag) + "\n---\n"
                         + (p.stderr or p.stdout or "")[-1200:])

    hits = sorted(glob.glob(os.path.join(a.out_dir, (name or "dl") + ".wav")))
    if not hits:
        hits = sorted(glob.glob(os.path.join(a.out_dir, (name or "dl") + ".*")))
    if not hits:
        raise SystemExit("下载完成但找不到输出文件")
    f = hits[0]
    if not f.lower().endswith(".wav"):
        wav = os.path.splitext(f)[0] + ".wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f, "-ac", "2",
                        "-ar", "44100", "-c:a", "pcm_s16le", wav], check=True)
        f = wav
    info = probe(f)
    log("probe", f"{f} {info['duration']}s {info['sample_rate']}Hz")
    if a.no_upload:
        print("LOCAL", f)
        return
    key = f"inbox/{(name or os.path.splitext(os.path.basename(f))[0])}.wav"
    r2_put(f, key, ctype="audio/wav")
    print("R2KEY", key)


if __name__ == "__main__":
    main()
