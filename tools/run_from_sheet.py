#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「素材清单.txt」变成一次云端批量任务（本机运行）

用法：
  python tools/run_from_sheet.py                          # 默认读 E:\小鱼歌曲翻唱文件夹原版音乐\素材清单.txt
  python tools/run_from_sheet.py --sheet 别的清单.txt
  python tools/run_from_sheet.py --dry-run                # 只打印 plan，不触发
  python tools/run_from_sheet.py --seconds 20             # 冒烟

清单格式（一行一首，| 分隔）：
  歌名 | 原唱链接 | 伴奏链接 | 清唱链接 | 备注
  后三段可空、可写 -；本地文件路径也认（会先传 R2 inbox/ 再跑）

做的事：
  1. 解析清单
  2. 本地音频 → 传 R2 inbox/<歌名>_<角色>.wav
  3. 生成 plan.json → gh workflow run cover -f plan=<json>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DEFAULT_SHEET = r"E:\小鱼歌曲翻唱文件夹原版音乐\素材清单.txt"
REPO = os.environ.get("COVER_REPO", "493764654/cover-cloud")
AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")


def parse_sheet(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("=") or line.startswith("-"):
                if not line.strip("-=").strip():
                    continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                print(f"[跳过] 第 {ln} 行字段不足 2 段: {line[:60]}")
                continue
            title = parts[0].strip() or f"track{len(out) + 1}"
            vocal = _norm(parts[1] if len(parts) > 1 else "")
            inst = _norm(parts[2] if len(parts) > 2 else "")
            acap = _norm(parts[3] if len(parts) > 3 else "")
            note = parts[4] if len(parts) > 4 else ""
            if not (vocal or inst or acap):
                print(f"[跳过] 第 {ln} 行没有任何链接: {title}")
                continue
            out.append({"title": title, "vocal": vocal, "inst": inst, "acap": acap,
                        "note": note.strip(), "line": ln})
    return out


def _norm(v: str) -> str:
    v = (v or "").strip()
    if v in ("", "-", "—", "无", "空"):
        return ""
    if v.startswith(("http://", "https://")):
        return "url:" + v
    if os.path.exists(v):
        return v
    return v          # 可能是 r2:key 或写错的路径，留给 pipeline 报错


def upload_local(path: str, title: str, role: str) -> str:
    from common import r2_put
    key = f"inbox/{_slug(title)}_{role}.wav"
    if not path.lower().endswith(".wav"):
        wav = os.path.join(os.path.dirname(path), f"_to_r2_{role}.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "2",
                        "-ar", "44100", "-c:a", "pcm_s16le", wav], check=True)
        path = wav
    r2_put(path, key, ctype="audio/wav")
    return "r2:" + key


def _slug(s: str, limit: int = 30) -> str:
    keep = [c for c in s if c.isalnum() or c in "-_"]
    return ("".join(keep) or "track")[:limit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--seconds", default="0")
    ap.add_argument("--steps", default="25")
    ap.add_argument("--seg", default="24")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.sheet):
        sys.exit(f"找不到清单: {a.sheet}")
    rows = parse_sheet(a.sheet)
    if not rows:
        sys.exit("清单里没有可执行的行")
    print(f"解析到 {len(rows)} 首")

    plan = []
    for r in rows:
        item = {"title": r["title"]}
        for role, field in (("vocal", "vocal"), ("inst", "inst"), ("acap", "acap")):
            v = r[field]
            if not v:
                continue
            if v.startswith(("http", "r2:")):
                item[role] = v
            elif os.path.exists(v):
                item[role] = upload_local(v, r["title"], role) if not a.dry_run else v
            else:
                print(f"[警告] {r['title']} 的{role}既不是链接也不是本地文件: {v}")
        if not item.get("vocal") and not item.get("acap"):
            print(f"[跳过] {r['title']} 缺人声源")
            continue
        plan.append(item)

    blob = json.dumps(plan, ensure_ascii=False)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if a.dry_run:
        print("\n[dry-run] 未触发。")
        return
    if not plan:
        sys.exit("没有可跑的条目")
    if len(blob) > 60000:
        sys.exit("plan 太大（>60KB），分批跑吧")

    cmd = ["gh", "workflow", "run", "cover", "-R", REPO,
           "-f", "mode=cover", "-f", "plan=" + blob,
           "-f", "seconds=" + a.seconds, "-f", "steps=" + a.steps, "-f", "seg=" + a.seg,
           "-f", "targets=-"]
    print("\n触发云端：", " ".join(cmd[:6]), "...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("触发失败:\n" + (r.stderr or r.stdout)[-800:])
    print("已提交。看进度： gh run list -R", REPO, "-L 3")


if __name__ == "__main__":
    main()
