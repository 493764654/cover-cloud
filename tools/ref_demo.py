#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用新干声当音色，做一小段换声试听。

用法：python ref_demo.py <ref_vocals.wav> <song_vocals.wav> <out.mp3> [start] [dur]
"""
import os
import subprocess
import sys

sys.path.insert(0, r"D:\DSHgongzuoqualiyun\cloud\cover-cloud\scripts")
import numpy as np  # noqa: E402
import wave  # noqa: E402

FF = r"C:\Users\Administrator\Downloads\_refwork"


def wav_read(p):
    with wave.open(p) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x, sr


def best_window(x, sr, dur=28.0):
    W = int(dur * sr)
    if len(x) <= W:
        return 0.0
    step = sr // 2
    best = None
    for s in range(0, len(x) - W, step):
        seg = x[s:s + W]
        hop = sr
        n = len(seg) // hop
        db = np.array([20 * np.log10(max(np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2)), 1e-9))
                       for i in range(n)])
        clip = (np.abs(seg) >= 0.999).mean()
        sil = (db < -45).mean()
        score = clip * 2000 + sil * 60 + db.std() * 0.4 + max(0, (np.median(db) + 30)) * 0.5
        if best is None or score < best[0]:
            best = (score, s, clip, sil, np.median(db), db.std())
    print(f"  最佳窗口 {best[1]/sr:.1f}s 削波{best[2]*100:.3f}% 静音{best[3]*100:.0f}% "
          f"中位{best[4]:.1f}dB 起伏{best[5]:.1f}")
    return best[1] / sr


def main():
    ref_src, song_src, out = sys.argv[1], sys.argv[2], sys.argv[3]
    start = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
    dur = float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
    os.makedirs(FF, exist_ok=True)

    print("[1] 分析参考干声，挑最干净窗口")
    x, sr = wav_read(ref_src)
    ss = best_window(x, sr, 28.0)
    ref_wav = os.path.join(FF, "ref_pick.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-t", "28",
                    "-i", ref_src, "-af", "loudnorm=I=-16:TP=-1.5:LRA=9", "-ar", "44100",
                    "-ac", "1", ref_wav], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ref_wav,
                    "-c:a", "libmp3lame", "-b:a", "320k", os.path.join(FF, "ref_pick.mp3")], check=True)

    print(f"[2] 切演唱片段 {start:.0f}s +{dur:.0f}s 做换声测试")
    seg = os.path.join(FF, "sing_seg.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.0f}",
                    "-i", song_src, "-ar", "44100", "-ac", "2", seg], check=True)

    print("[3] Seed-VC 换声（25 步，约 3 分钟）")
    from pipeline import vc_seedvc
    vc = vc_seedvc(seg, ref_wav, FF, seg=20.0, steps=25, fp16="True", semi=0)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vc,
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=11", "-c:a", "libmp3lame", "-b:a", "320k", out],
                   check=True)
    print("DONE", out, os.path.getsize(out), "bytes")
    print("REF", os.path.join(FF, "ref_pick.mp3"))


if __name__ == "__main__":
    main()
