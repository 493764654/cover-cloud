#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E:\\小鱼歌曲翻唱文件夹原版音乐 → 翻唱（只认这一个文件夹）

新流程（2026-10-30 定）：只要一条「音质好的原唱」，不需要伴奏、不需要对齐移调。
  - 文件名带「伴奏/纯音乐/instrumental/karaoke」→ 跳过（没原唱跑不了）
  - 带「微醺/创作/remix/dj/加速/改词/串烧/翻唱版」→ 跳过（怕误跑创作版）
  - 其它音频（含没写「原唱」二字的）→ 默认当原唱，直接跑
  - 同一首歌多个文件 → 取时长最长的；和指纹库重复 → 跳过

用法：
  python run_folder.py --dry-run          # 只看会跑什么
  python run_folder.py                    # 本机 GPU 跑
  python run_folder.py --watch            # 常驻：丢进文件夹就自动跑
  python run_folder.py --only 冷雨夜
  python run_folder.py --force 冷雨夜     # 忽略指纹去重
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = r"E:\小鱼歌曲翻唱文件夹原版音乐"
OUT_BASE = r"C:\Users\Administrator\Downloads\cover_out"
FP_DB = os.path.join(OUT_BASE, "fingerprints.json")
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ape", ".aac", ".ogg"}
REF = r"D:\DSHgongzuoqualiyun\artifacts\ref\xiaoyu_ref_v2.wav"
PY = r"D:\DSHgongzuoqualiyun\cover\win\venv\Scripts\python.exe"
PIPE = r"D:\DSHgongzuoqualiyun\cloud\cover-cloud\scripts\pipeline.py"

INST_ONLY = ("伴奏", "纯音乐", "instrumental", "karaoke", "inst版", "off vocal")
BLOCK = ("微醺", "创作", "翻唱版", "remix", "dj", "加速", "改词", "自剪", "串烧", "鬼畜", "变声",
         "音色", "参考音", "reference", "voice_ref")
MIN_SEC, MAX_SEC = 60, 600
SETTLE_SEC = 30   # 文件落盘后至少静置 30 秒才跑，避免跑到半个文件
MIN_KBPS = 128


def sh(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def probe(path):
    r = sh("ffprobe", "-v", "error", "-show_entries", "format=duration,bit_rate",
           "-of", "json", path)
    try:
        f = json.loads(r.stdout or "{}").get("format", {})
        return float(f.get("duration") or 0), int(f.get("bit_rate") or 0) // 1000
    except Exception:  # noqa: BLE001
        return 0.0, 0


def peak_db(path):
    r = sh("ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-")
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    n = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return (float(m.group(1)) if m else -99, float(n.group(1)) if n else -99)


def chroma_fp(path, sec=120, sr=11025):
    """和声指纹：12 维色度 + 时长，用于识别「同一首歌不同来源」。"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", str(sec), "-ac", "1",
                        "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768
    if len(x) < sr:
        return None
    nfft, hop = 8192, 2048
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    mask = (freqs > 80) & (freqs < 2000)
    bins = np.round(12 * np.log2(freqs[mask] / 440.0) + 69).astype(int) % 12
    C = np.zeros(12)
    n = (len(x) - nfft) // hop
    for i in range(n):
        s = x[i * hop:i * hop + nfft] * np.hanning(nfft)
        m = np.abs(np.fft.rfft(s))[mask]
        np.add.at(C, bins, m * m)
    v = C / (np.linalg.norm(C) + 1e-9)
    return [float(t) for t in v]


def load_fp():
    if os.path.exists(FP_DB):
        try:
            return json.load(open(FP_DB, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def save_fp(db):
    os.makedirs(os.path.dirname(FP_DB), exist_ok=True)
    json.dump(db, open(FP_DB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def fp_dup(vec, dur, db):
    import numpy as np
    if not vec:
        return None
    for sid, e in db.items():
        if abs(e.get("dur", 0) - dur) > 6:
            continue
        v2 = e.get("chroma")
        if not v2:
            continue
        cos = float(np.dot(np.array(vec), np.array(v2)))
        if cos > 0.985:
            return sid, cos
    return None


def classify(name):
    low = name.lower()
    if any(b in low for b in BLOCK):
        return "block"
    if any(b in low for b in INST_ONLY):
        return "inst_only"
    return "vocal"


SCAN_ROOT = ROOT


def scan():
    cands, skipped = {}, []
    for f in sorted(os.listdir(SCAN_ROOT)):
        p = os.path.join(SCAN_ROOT, f)
        if not os.path.isfile(p) or os.path.splitext(f)[1].lower() not in AUDIO_EXT:
            continue
        age = time.time() - os.path.getmtime(p)
        if age < SETTLE_SEC:
            skipped.append((f, f"刚下载/还在写入（{age:.0f}s < {SETTLE_SEC}s）"))
            continue
        k = classify(f)
        if k == "block":
            skipped.append((f, "疑似创作版"))
            continue
        if k == "inst_only":
            skipped.append((f, "只有伴奏/纯音乐"))
            continue
        title = re.sub(r"[（(\[【\s_-]*(原唱|原曲|无损|高音质|完整版)[）)\]】\s_-]*", "", os.path.splitext(f)[0]).strip(" -_·")
        cands.setdefault(title, []).append(p)
    return cands, skipped


def gate(path):
    dur, kbps = probe(path)
    peak, mean = peak_db(path)
    problems = []
    if dur < MIN_SEC:
        problems.append(f"太短 {dur:.0f}s")
    if dur > MAX_SEC:
        problems.append(f"太长 {dur/60:.1f}min")
    if kbps and kbps < MIN_KBPS:
        problems.append(f"码率只有 {kbps}kbps")
    if peak >= -0.1:
        problems.append(f"已削波 peak {peak:.1f}dB")
    if mean < -30:
        problems.append(f"音量太低 均 {mean:.1f}dB")
    return (not problems), problems, dur, kbps


def run_one(title, path, a, db):
    ok, problems, dur, kbps = gate(path)
    print(f"  时长 {dur:.0f}s 码率 {kbps}kbps")
    if not ok:
        print("  [X] 质量不合格：" + "；".join(problems))
        return False
    sid = "s" + hashlib.md5(title.encode("utf-8")).hexdigest()[:6]
    vec = chroma_fp(path)
    dup = fp_dup(vec, dur, db)
    if dup and not a.force:
        print(f"  [SKIP] 指纹重复：和 {dup[0]} 相似度 {dup[1]:.3f}，跳过（要跑加 --force）")
        return False
    work = os.path.join(OUT_BASE, a.channel)
    cmd = [PY, PIPE, "--source", path, "--sid", sid, "--title", title,
           "--work", work, "--sec", "0", "--steps", str(a.steps), "--seg", str(a.seg),
           "--channel", a.channel]
    if a.ref and os.path.exists(a.ref):
        cmd += ["--ref", a.ref]
    elif os.path.exists(REF):
        cmd += ["--ref", REF]
    print("  -> " + " ".join(os.path.basename(c) for c in cmd[:6]))
    t0 = time.time()
    r = subprocess.run(cmd)
    if r.returncode == 0:
        db[sid] = {"title": title, "dur": dur, "chroma": vec,
                   "at": time.strftime("%Y-%m-%d %H:%M"), "elapsed": int(time.time() - t0),
                   "channel": a.channel}
        save_fp(db)
        print(f"  [OK] 完成 {int(time.time()-t0)}s -> {os.path.join(work, sid)}")
        return True
    print("  [X] 失败，返回码", r.returncode)
    return False


def prepare(a):
    cands, skipped = scan()
    ready = {}
    for title, paths in cands.items():
        if a.only and a.only not in title:
            continue
        if len(paths) > 1:
            paths.sort(key=lambda p: probe(p)[0], reverse=True)
            print(f"[多版本] {title}: 取最长的 {os.path.basename(paths[0])}")
        ready[title] = paths[0]
    for f, why in skipped:
        print(f"[跳] {f}（{why}）")
    return ready


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--watch", action="store_true", help="常驻监听文件夹")
    ap.add_argument("--dir", default="", help="只扫 E 盘下的这个子文件夹（本机/GitHub/飞桨）")
    ap.add_argument("--channel", default="local")
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true", help="忽略指纹去重")
    ap.add_argument("--ref", default="")
    ap.add_argument("--semi", type=int, default=0)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seg", type=float, default=24)
    a = ap.parse_args()

    global SCAN_ROOT
    if a.dir:
        cand = [a.dir, a.dir.lower(), a.dir.capitalize()]
        for d in cand:
            p = os.path.join(ROOT, d)
            if os.path.isdir(p):
                SCAN_ROOT = p
                break
        else:
            raise SystemExit(f"没有这个子文件夹：{a.dir}")
        print(f"扫描子目录：{SCAN_ROOT}")
        if not a.channel or a.channel == "local":
            a.channel = {"本机": "local", "github": "github", "GitHub": "github",
                         "飞桨": "aistudio", "aistudio": "aistudio"}.get(a.dir, a.channel)

    db = load_fp()
    done = set()
    while True:
        ready = prepare(a)
        todo = {k: v for k, v in ready.items() if k not in done}
        if todo:
            print(f"\n待跑 {len(todo)} 首：")
            for k, v in todo.items():
                print(f"[跑] {k}: {os.path.basename(v)}")
            if a.dry_run:
                return
            for title, path in todo.items():
                print(f"\n=== {title} ===")
                if run_one(title, path, a, db):
                    done.add(title)
        else:
            if a.dry_run or not a.watch:
                print("没有待跑的歌")
                return
        if not a.watch:
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
