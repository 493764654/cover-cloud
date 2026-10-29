#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端翻唱流水线（GitHub Actions / 飞桨通用，CPU 可跑）

  源（R2 key / URL / 本地文件）
    → 规范化 44.1k
    → demucs(htdemucs) 分离 vocals / instrumental
    → Seed-VC 分段换声（24s 段 + 交叉淡化，消除长曲掉音）
    → 空气感 → 与人声混音 → -14 LUFS 320k
    → 三条产物传 R2：<sid>_cover.mp3 / <sid>_cover_air.mp3 / <sid>_acapella.mp3
    → 合并写根 manifest.json（variants.normal / variants.acapella）

用法：
  python scripts/pipeline.py --source r2:inbox/s001.mp3 --sid s001 --title 晴天
  python scripts/pipeline.py --source "url:https://www.youtube.com/watch?v=xxx" --sid s010 --title 男儿当自强
  python scripts/pipeline.py --source /tmp/a.mp3 --sid s001 --title x --no-upload --work /tmp/w
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEEDVC = os.path.join(ROOT, "vendor", "seedvc")
sys.path.insert(0, HERE)

from common import (ff, log, loudnorm_two_pass, measure_loudness, next_id, probe,  # noqa: E402
                    r2_get, r2_list, r2_put, upsert_manifest)


# ------------------------------------------------------------------ 取源
def fetch_source(src: str, wd: str) -> str:
    """r2:key / url:http... / 本地路径 → 统一返回源文件路径。"""
    if src.startswith("r2:"):
        return r2_get(src[3:], os.path.join(wd, "src_raw" + os.path.splitext(src[3:])[1]))
    if src.startswith("url:"):
        url = src[4:]
        out = os.path.join(wd, "src_raw.%(ext)s")
        cmd = [sys.executable, "-m", "yt_dlp", "-x", "--audio-format", "wav",
               "--audio-quality", "0", "-o", out, url]
        log("download", url)
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError("yt-dlp 失败:\n" + (p.stderr or "")[-1200:])
        hits = sorted(glob.glob(os.path.join(wd, "src_raw.*")))
        if not hits:
            raise RuntimeError("下载成功但找不到文件")
        return hits[0]
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    return src


# ------------------------------------------------------------------ 分离
def separate(src: str, wd: str, seconds: float = 0, demucs_seg: float = 7) -> tuple[str, str]:
    """demucs htdemucs 两分轨（CPU 可跑）。返回 (vocals.wav, instrumental.wav)。"""
    import torch
    import torchaudio
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    src44 = os.path.join(wd, "src44.wav")
    trim = ["-t", str(seconds)] if seconds else []
    ff(*trim, "-i", src, "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", src44)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = get_model("htdemucs")
    model.to(dev).eval()
    wav, sr = torchaudio.load(src44)
    if sr != model.samplerate:
        wav = torchaudio.functional.resample(wav, sr, model.samplerate)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    wav = wav.to(dev)
    ref = wav.mean(0)
    x = (wav - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        out = apply_model(model, x[None], segment=demucs_seg, overlap=0.25, progress=False)[0]
    out = out * ref.std() + ref.mean()
    vi = model.sources.index("vocals")
    vocals, inst = out[vi], out.sum(0) - out[vi]
    vp, ip = os.path.join(wd, "vocals.wav"), os.path.join(wd, "instrumental.wav")
    torchaudio.save(vp, vocals.cpu(), model.samplerate)
    torchaudio.save(ip, inst.cpu(), model.samplerate)
    log("separate", f"dev={dev} {probe(vp)['duration']}s")
    return vp, ip


# ------------------------------------------------------------------ 换声
def _read_wav(path: str):
    alt = path
    try:
        with wave.open(path, "rb") as w:
            sr, ch = w.getframerate(), w.getnchannels()
            raw = w.readframes(w.getnframes())
    except wave.Error:
        alt = path.rsplit(".", 1)[0] + "_s16.wav"
        ff("-i", path, "-c:a", "pcm_s16le", alt)
        with wave.open(alt, "rb") as w:
            sr, ch = w.getframerate(), w.getnchannels()
            raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return (x.reshape(-1, ch) if ch > 1 else x[:, None]), sr


def _write_wav(path: str, x: np.ndarray, sr: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def vc_seedvc(vocals: str, ref: str, wd: str, seg: float = 24.0, overlap: float = 1.0,
              steps: int = 25, cfg: float = 0.7, fp16: str = "False") -> str:
    """分段换声 + 交叉淡化拼接 → vc_full.wav（干声）。"""
    out_dir = os.path.join(wd, "vc")
    os.makedirs(out_dir, exist_ok=True)
    x, sr = _read_wav(vocals)
    total = len(x) / sr
    tmp = os.path.join(out_dir, "_seg")
    os.makedirs(tmp, exist_ok=True)
    pos, pieces, i = 0.0, [], 0
    while pos < total:
        dur = min(seg + overlap, total - pos)
        if dur < 2:
            break
        i += 1
        seg_wav = os.path.join(tmp, f"seg{i:02d}.wav")
        ff("-ss", f"{pos:.3f}", "-t", f"{dur:.3f}", "-i", vocals, "-c:a", "pcm_s16le", seg_wav)
        out = os.path.join(tmp, f"out{i:02d}")
        cmd = [sys.executable, "inference.py", "--source", seg_wav, "--target", ref,
               "--output", out, "--diffusion-steps", str(steps), "--length-adjust", "1.0",
               "--inference-cfg-rate", str(cfg), "--f0-condition", "True",
               "--auto-f0-adjust", "False", "--semi-tone-shift", "0",
               "--checkpoint", "ckpt/DiT_v2.pth", "--config", "configs/presets/config_local.yml",
               "--fp16", fp16]
        p = subprocess.run(cmd, cwd=SEEDVC, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8",
                                "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "4")})
        if p.returncode != 0:
            raise RuntimeError(f"段 {i} 推理失败:\n" + (p.stderr or p.stdout or "")[-1500:])
        wavs = sorted(glob.glob(os.path.join(out, "*.wav")))
        if not wavs:
            raise RuntimeError(f"段 {i} 无输出")
        y, _ = _read_wav(wavs[0])
        pieces.append((pos, y, dur))
        log("vc", f"seg{i:02d} {pos:.1f}..{pos + dur:.1f}s {y.shape}")
        if pos + seg >= total:
            break
        pos += seg

    out_len = int(total * sr)
    acc = np.zeros((out_len, x.shape[1]), dtype=np.float32)
    for k, (p0, y, dur) in enumerate(pieces):
        s = int(p0 * sr)
        e = min(s + len(y), out_len)
        chunk = y[: e - s]
        if k == 0:
            acc[s:e] += chunk
            continue
        ov = min(int(min(overlap, dur) * sr), len(chunk), e - s)
        fade = np.linspace(0, 1, ov, dtype=np.float32)[:, None]
        acc[s:s + ov] = acc[s:s + ov] * (1 - fade) + chunk[:ov] * fade
        acc[s + ov:e] = chunk[ov:]
    vc = os.path.join(out_dir, "vc_full.wav")
    _write_wav(vc, acc, sr)
    log("vc", f"拼接完成 {out_len / sr:.1f}s -> {vc}")
    return vc


# ------------------------------------------------------------------ 混音
def mix(vc: str, instrumental: str, wd: str, no_loudnorm: bool = False):
    """空气感 + 混音 → (混音 mp3, 空气感 mp3 或 None, LUFS)"""
    air = os.path.join(wd, "air.wav")
    ff("-i", vc, "-af",
       "aexciter=amount=1.6:drive=7:freq=5000:blend=0,highshelf=f=7000:g=2:width_type=q:w=0.7",
       "-ar", "44100", air)
    mixed = os.path.join(wd, "mixed.wav")
    fc = ("[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=2dB[v];"
          "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[i];"
          "[i][v]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
          "volume=-2dB,alimiter=limit=0.94[m]")
    ff("-i", air, "-i", instrumental, "-filter_complex", fc, "-map", "[m]",
       "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", mixed)

    cover = os.path.join(wd, "cover.mp3")
    air_mp3 = os.path.join(wd, "cover_air.mp3")
    if no_loudnorm:
        ff("-i", mixed, "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2",
           "-write_xing", "1", "-id3v2_version", "3", cover)
        shutil.copyfile(cover, air_mp3)
        return cover, air_mp3, measure_loudness(cover)
    log("mix", f"mix LUFS {measure_loudness(mixed)}")
    loud = loudnorm_two_pass(mixed, cover, I=-14.0)
    shutil.copyfile(cover, air_mp3)     # 空气感已并入混音链，主版本即带空气感
    return cover, air_mp3, loud


def export_acapella(vc: str, wd: str) -> str:
    out = os.path.join(wd, "acapella.mp3")
    loud = loudnorm_two_pass(vc, out, I=-14.0)
    log("acapella", f"{loud} LUFS -> {out}")
    return out


# ------------------------------------------------------------------ 主流程
def run_one(source: str, sid: str, title: str, ref: str, work: str, seconds: float,
            steps: int, seg: float, no_upload: bool, reuse: bool = False) -> dict:
    wd = os.path.join(work, sid)
    os.makedirs(wd, exist_ok=True)
    log("start", f"{sid} ← {source[:90]}")

    vp, ip = os.path.join(wd, "vocals.wav"), os.path.join(wd, "instrumental.wav")
    if reuse and os.path.exists(vp) and os.path.exists(ip):
        vocals, inst = vp, ip
        log("separate", "复用已有 stems（--reuse）")
    else:
        src = fetch_source(source, wd)
        vocals, inst = separate(src, wd, seconds=seconds)
    vc = vc_seedvc(vocals, ref, wd, seg=seg, steps=steps)
    cover, air, loud = mix(vc, inst, wd)
    acap = export_acapella(vc, wd)
    info = probe(cover)

    result = {"sid": sid, "title": title, "duration": info["duration"],
              "loudness": loud, "cover": cover, "air": air, "acapella": acap, "uploaded": False}
    if no_upload:
        log("done", f"{sid} (no-upload) {cover}")
        return result

    base = os.environ.get("R2_PUBLIC_BASE", "").rstrip("/")
    keys = {suf: f"songs/{sid}/{sid}_{suf}" for suf in ("cover", "cover_air", "acapella")}
    r2_put(cover, keys["cover"] + ".mp3")
    r2_put(air, keys["cover_air"] + ".mp3")
    r2_put(acap, keys["acapella"] + ".mp3")
    urls = [f"{base}/{keys[k]}.mp3" if base else "" for k in ("cover", "cover_air")]
    acap_url = f"{base}/{keys['acapella']}.mp3" if base else None
    upsert_manifest(sid, title, [u for u in urls if u] or urls, acap_url, info, loud,
                    keys["cover"] + ".mp3")
    result["uploaded"] = True
    log("done", f"{sid} -> {base}/{keys['cover']}.mp3")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="r2:key | url:http... | 本地路径")
    ap.add_argument("--sid", default="", help="song_id，缺省自动取下一个 sXXX")
    ap.add_argument("--title", default="")
    ap.add_argument("--ref", default="", help="参考音色音频；缺省从 R2 assets/ref/cover_voice.mp3 取")
    ap.add_argument("--work", default=os.environ.get("WORK", "/tmp/coverwork"))
    ap.add_argument("--seconds", type=float, default=0, help="只处理前 N 秒（冒烟用）")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seg", type=float, default=24.0, help="换声分段长度（秒）")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="复用 work/<sid>/ 下已有 vocals/instrumental")
    ap.add_argument("--list-inbox", action="store_true", help="列出 R2 inbox/ 下的音频后退出")
    a = ap.parse_args()

    if a.list_inbox:
        for k in r2_list("inbox/"):
            print(k)
        return

    os.makedirs(a.work, exist_ok=True)
    ref = a.ref
    if not ref or not os.path.exists(ref):
        ref = r2_get(os.environ.get("R2_REF_KEY", "assets/ref/cover_voice.mp3"),
                     os.path.join(a.work, "ref_voice.mp3"))

    sid = a.sid
    if not sid:
        from common import load_manifest
        sid = next_id(load_manifest().get("songs", []))
    title = a.title or os.path.splitext(os.path.basename(a.source.replace("r2:", "")))[0]

    res = run_one(a.source, sid, title, ref, a.work, a.seconds, a.steps, a.seg, a.no_upload,
                  reuse=a.reuse)
    print("RESULT " + str(res), flush=True)


if __name__ == "__main__":
    main()
