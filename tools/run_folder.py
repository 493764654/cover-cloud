#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只扫 E:\\小鱼歌曲翻唱文件夹原版音乐，配对「原唱 / 伴奏」后跑翻唱。

铁律（用户 2026-10-29 指定）：
  - 只读这一个文件夹，别处一律不看；
  - 文件名必须带标记词才认，其它一律跳过（防止误跑小鱼的创作版/微醺版）；
  - 同一首歌的原唱+伴奏才跑；只有伴奏的直接跳过。

用法：
  python run_folder.py --dry-run      # 只打印配对结果
  python run_folder.py                # 本地 GPU 跑（调用 scripts/pipeline.py）
  python run_folder.py --cloud        # 造 plan.json 并触发 GitHub workflow
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = r"E:\小鱼歌曲翻唱文件夹原版音乐"
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ape", ".aac", ".ogg"}
VOCAL_MARK = ("原唱", "原曲", "原版原唱", "人声")
INST_MARK = ("伴奏", "instrumental", "inst", "纯伴奏")
ACAP_MARK = ("清唱", "干声", "acapella", "纯人声")
# 出现这些词一律不跑（创作版/二次创作/改编）
BLOCK_MARK = ("微醺", "创作", "翻唱版", "remix", "dj", "加速", "改词", "自剪", "串烧")

PY = r"D:\DSHgongzuoqualiyun\cover\win\venv\Scripts\python.exe"
PIPE = r"D:\DSHgongzuoqualiyun\cloud\cover-cloud\scripts\pipeline.py"


def kind(name: str) -> str:
    low = name.lower()
    if any(b in low for b in BLOCK_MARK):
        return "block"
    if any(m in low for m in ACAP_MARK):
        return "acap"
    if any(m in low for m in INST_MARK):
        return "inst"
    if any(m in low for m in VOCAL_MARK):
        return "vocal"
    return "unknown"


def strip_mark(name: str) -> str:
    base = os.path.splitext(name)[0]
    for m in ACAP_MARK + INST_MARK + VOCAL_MARK:
        base = re.sub(rf"[（(\[【\s_-]*{m}[）)\]】\s_-]*", "", base, flags=re.I)
    return base.strip(" -_·")


def scan():
    songs: dict[str, dict] = {}
    skipped = []
    for f in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p) or os.path.splitext(f)[1].lower() not in AUDIO_EXT:
            continue
        k = kind(f)
        if k in ("unknown", "block"):
            skipped.append((f, "无原唱/伴奏标记" if k == "unknown" else "疑似创作版"))
            continue
        key = strip_mark(f)
        songs.setdefault(key, {})[k] = p
    return songs, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seg", type=float, default=24)
    ap.add_argument("--sec", type=float, default=0, help="调试用：只跑前 N 秒")
    ap.add_argument("--only", default="", help="只跑歌名包含该字串的（其余跳过）")
    ap.add_argument("--ref", default="", help="参考音色文件；空则用 R2 上的 assets/ref/cover_voice.mp3")
    ap.add_argument("--semi", type=int, default=0, help="人声移调半音（伴奏降1写 -1）")
    a = ap.parse_args()

    songs, skipped = scan()
    print(f"目录：{ROOT}")
    ready = {k: v for k, v in songs.items() if "vocal" in v}
    if a.only:
        ready = {k: v for k, v in ready.items() if a.only in k}
    for k, v in ready.items():
        print(f"[跑] {k}: " + " + ".join(sorted(v)))
    for k, v in songs.items():
        if "vocal" not in v:
            print(f"[跳] {k}: 只有伴奏，没原唱")
    for f, why in skipped:
        print(f"[跳] {f}（{why}）")
    if not ready:
        print("没有可跑的（需要文件名带「原唱/原曲」，伴奏带「伴奏」）")
        return
    if a.dry_run:
        return

    work = os.path.join(r"C:\Users\Administrator\Downloads\cover_out", a.channel)
    ok = 0
    for i, (title, v) in enumerate(ready.items()):
        sid = "s" + hashlib.md5(title.encode("utf-8")).hexdigest()[:6]
        cmd = [PY, PIPE, "--source", v["vocal"], "--sid", sid, "--title", title,
               "--work", work, "--sec", str(a.sec), "--steps", str(a.steps),
               "--seg", str(a.seg), "--channel", a.channel]
        if a.ref:
            cmd += ["--ref", a.ref]
        if a.semi:
            cmd += ["--semi", str(a.semi)]
        if "inst" in v:
            cmd += ["--inst", v["inst"]]
        if "acap" in v:
            cmd += ["--acapella", v["acap"]]
        if a.cloud:
            print("云端模式暂未接（先本地跑通）：", " ".join(cmd))
            continue
        print(f"\n=== {title} ===")
        r = subprocess.run(cmd)
        ok += 1 if r.returncode == 0 else 0
    print(f"\n完成 {ok}/{len(ready)}，产物在 {work}")


if __name__ == "__main__":
    main()
