#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共件：日志 / ffmpeg 封装 / 响度 / R2 客户端 / manifest 合并。

云端（GitHub Actions Linux）与本地都可用；不依赖任何 Windows 路径。
环境变量：
  R2_ENDPOINT R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_REGION R2_PUBLIC_BASE
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

T0 = time.time()
MANIFEST_KEY = "manifest.json"


def log(stage: str, msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {stage:<10} {msg}", flush=True)
    _report(stage, msg)


_last_push = [0.0]


def _report(stage: str, msg: str) -> None:
    """把进度写到 R2（R2_STATUS_KEY），用于远端实时观察长任务卡在哪一步。"""
    key = os.environ.get("R2_STATUS_KEY")
    if not key:
        return
    now = time.time()
    if now - _last_push[0] < 3:
        return
    _last_push[0] = now
    try:
        s3, bucket = r2()
        body = json.dumps({"t": round(now - T0, 1), "stage": stage, "msg": str(msg)[:400],
                           "at": time.strftime("%H:%M:%S")}, ensure_ascii=False).encode("utf-8")
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    except Exception:  # noqa: BLE001
        pass


def ff(*args: str) -> None:
    """ffmpeg 静默执行，失败抛异常。"""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 失败: " + " ".join(args)[:200] + "\n" + (p.stderr or "")[-1500:])


def measure_loudness(path: str):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, encoding="utf-8", errors="replace")
    m = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", p.stderr or "")
    return float(m[-1]) if m else None


def loudnorm_two_pass(src: str, dst: str, I: float = -14.0, TP: float = -1.5, LRA: float = 11.0):
    """两遍 loudnorm → 320k / 44.1k / stereo。返回成品 LUFS。"""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af",
                        f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
                        "-f", "null", "-"], capture_output=True,
                       encoding="utf-8", errors="replace")
    try:
        txt = (p.stderr or "")[(p.stderr or "").rfind("{"):]
        j = json.loads(txt[: txt.rfind("}") + 1])
    except Exception:  # noqa: BLE001
        log("loudnorm", "两遍测量失败，退回单遍")
        ff("-i", src, "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}",
           "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2",
           "-write_xing", "1", "-id3v2_version", "3", dst)
        return measure_loudness(dst)
    filt = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:linear=true:print_format=json:"
            f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
            f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:"
            f"offset={j['target_offset']}")
    ff("-i", src, "-af", filt, "-c:a", "libmp3lame", "-b:a", "320k",
       "-ar", "44100", "-ac", "2", "-write_xing", "1", "-id3v2_version", "3", dst)
    return measure_loudness(dst)


def probe(path: str) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,bit_rate", "-show_entries", "stream=sample_rate",
                        "-of", "json", path], capture_output=True, encoding="utf-8", errors="replace")
    try:
        j = json.loads(p.stdout or "{}")
        f, st = j.get("format", {}), (j.get("streams") or [{}])[0]
        return {"duration": round(float(f.get("duration", 0) or 0), 2),
                "bitrate": int(f.get("bit_rate", 0) or 0),
                "sample_rate": int(st.get("sample_rate", 0) or 0)}
    except Exception:  # noqa: BLE001
        return {"duration": 0.0, "bitrate": 0, "sample_rate": 0}


# ----------------------------------------------------------------------------- R2
def r2():
    """返回 (client, bucket)。缺环境变量直接退出。"""
    miss = [k for k in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
            if not os.environ.get(k)]
    if miss:
        sys.exit("缺环境变量: " + ", ".join(miss))
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("R2_REGION", "auto"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    ), os.environ["R2_BUCKET"]


def r2_get(key: str, dst: str) -> str:
    """直接 get_object 流式落盘（不走 download_file 的 head_object，R2 上更稳）。"""
    s3, bucket = r2()
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        with open(dst, "wb") as f:
            shutil.copyfileobj(obj["Body"], f, 1024 * 1024)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"R2 读取失败 bucket={bucket} key={key!r}: {type(e).__name__}: {e}") from e
    log("r2", f"GET {key} -> {os.path.getsize(dst) / 1048576:.2f}MB")
    return dst


def r2_put(src: str, key: str, ctype: str = "audio/mpeg", cache: str = "public, max-age=31536000") -> None:
    s3, bucket = r2()
    t = time.time()
    s3.upload_file(src, bucket, key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})
    log("r2", f"PUT {key} {os.path.getsize(src) / 1048576:.2f}MB {time.time() - t:.1f}s")


def r2_list(prefix: str) -> list[str]:
    s3, bucket = r2()
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in r.get("Contents", [])]
        if not r.get("IsTruncated"):
            return keys
        token = r.get("NextContinuationToken")


def load_manifest() -> dict:
    s3, bucket = r2()
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)["Body"].read())
    except Exception:  # noqa: BLE001
        return {"count": 0, "songs": []}


def save_manifest(man: dict) -> None:
    s3, bucket = r2()
    man["songs"].sort(key=lambda s: str(s.get("song_id", "")))
    man["count"] = len(man["songs"])
    man["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    body = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=MANIFEST_KEY, Body=body,
                  ContentType="application/json", CacheControl="public, max-age=60")
    log("manifest", f"updated count={man['count']}")


def next_id(songs: list[dict]) -> str:
    mx = 0
    for s in songs:
        m = re.match(r"^s(\d+)$", str(s.get("song_id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"s{mx + 1:03d}"


def upsert_manifest(sid: str, title: str, normal_urls: list[str], acapella_url: str | None,
                    info: dict, loudness, key: str) -> dict:
    """写 manifest：variants.normal（混音版，可多条）+ variants.acapella（干声）。"""
    man = load_manifest()
    songs = man.setdefault("songs", [])
    variants: dict = {"normal": normal_urls}
    if acapella_url:
        variants["acapella"] = acapella_url
    item = {
        "song_id": sid,
        "title": title,
        "key": key,
        "url": normal_urls[0] if normal_urls else "",
        "duration": info.get("duration", 0),
        "bitrate": info.get("bitrate", 0),
        "sample_rate": info.get("sample_rate", 0),
        "loudness": round(loudness, 1) if isinstance(loudness, (int, float)) else None,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "variants": variants,
    }
    man["songs"] = [s for s in songs if s.get("song_id") != sid] + [item]
    save_manifest(man)
    return item
