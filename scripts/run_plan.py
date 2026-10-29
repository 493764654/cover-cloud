#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 plan.json 批量跑（workflow 内部用）

plan.json 结构：
  [{"title": "男儿当自强", "vocal": "url:https://...", "inst": "url:https://...", "acap": "", "sid": ""}]
字段全可空，vocal/inst/acap 支持 r2:key / url:http... / 本地路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import load_manifest, next_id, r2_get  # noqa: E402
from pipeline import run_one  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--ref", default="")
    ap.add_argument("--work", default=os.environ.get("WORK", "/tmp/coverwork"))
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seg", type=float, default=24.0)
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8-sig"))
    if isinstance(plan, dict):
        plan = plan.get("songs", [])
    os.makedirs(a.work, exist_ok=True)

    ref = a.ref
    if not ref or not os.path.exists(ref):
        ref = r2_get(os.environ.get("R2_REF_KEY", "assets/ref/cover_voice.mp3"),
                     os.path.join(a.work, "ref_voice.mp3"))

    man = load_manifest().get("songs", [])
    ok = fail = 0
    for i, it in enumerate(plan, 1):
        title = str(it.get("title") or f"track{i}").strip()
        sid = str(it.get("sid") or "").strip() or next_id(man + [{"song_id": f"s{999 - 0:03d}"}])
        if not it.get("sid"):
            # 每首跑完刷新一次，保证 sid 递增不撞
            man = load_manifest().get("songs", [])
            sid = next_id(man)
        print(f"=== [{i}/{len(plan)}] {title} -> {sid}", flush=True)
        try:
            run_one(it.get("vocal") or it.get("source") or "", sid, title, ref, a.work,
                    a.seconds, a.steps, a.seg, a.no_upload, reuse=a.reuse,
                    inst_src=it.get("inst", ""), acap_src=it.get("acap", ""))
            ok += 1
        except SystemExit as e:
            fail += 1
            print(f"SKIP {title}: {e}", flush=True)
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"FAIL {title}: {type(e).__name__}: {e}", flush=True)
    print(f"==== 完成 成功 {ok} / 失败 {fail}", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
