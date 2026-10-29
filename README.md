# cover-cloud · 云端翻唱流水线（GitHub Actions）

一套跑在 GitHub Actions 上的无人值守翻唱批处理：拉素材 → demucs 分离 → Seed-VC 换音色 → 混音母带 → 直传 Cloudflare R2，点歌插件不用改就能取到 `variants`。

本地 GPU 那条线（`D:\DSHgongzuoqualiyun\cover\svc_cover.sh`）保持不动；这里是它的 CPU 云端版，跑同一套 Seed-VC 权重与推理参数。

---

## 一、能力边界（先看这个，别期待错）

| 项 | 值 |
|---|---|
| runner | `ubuntu-latest`，4 vCPU / 16 GB / **无 GPU** |
| 免费额度 | 私有仓库 2000 分钟/月（≈33 h）；公开仓库不限但会公开代码 |
| 单首耗时 | 4 分钟歌 ≈ **20–45 分钟**（demucs CPU 5–8 min + Seed-VC 24 s 分段 15–35 min） |
| 单次 job 上限 | 6 小时；本工作流设 350 分钟，安全 |
| 并发 | 一个 job 串行跑多首（模型只加载一次）；月额度要并行就开第二个 workflow |
| 产物 | `songs/<sid>/<sid>_cover.mp3`、`_cover_air.mp3`、`_acapella.mp3` + 根 `manifest.json` |

> 首批前先跑一次 `seconds=20` 冒烟，确认 20 秒能出成品再挂整曲。

---

## 二、一次性准备（10 分钟）

### 1. 模型权重放进 R2（只传一次，之后走 Actions 缓存）

Seed-VC 权重约 2.4 GB（`DiT_v2.pth` 783 MB + `whisper-small` 927 MB + `bigvgan` 467 MB + `rmvpe.pt` 181 MB）。

```bash
# 在本机（Windows git-bash）
cd /d/DSHgongzuoqualiyun/cover/seedvc
tar -cf - --exclude='ckpt/v2' --exclude='ckpt/.cache' ckpt | xz -0 -T4 > /c/Users/Administrator/Downloads/seedvc_ckpt.tar.xz
# 上传
cd /d/DSHgongzuoqualiyun/cover && source ./env
python - <<'PY'
import os, boto3
from botocore.config import Config
s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                  aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
                  config=Config(signature_version="s3v4"))
s3.upload_file(r"C:\Users\Administrator\Downloads\seedvc_ckpt.tar.xz",
               os.environ["R2_BUCKET"], "models/seedvc/seedvc_ckpt.tar.xz")
print("ok")
PY
```

### 2. 参考音色放 R2

```bash
# 目标音色参考（8~30s 干净人声）
python -c "import os,boto3;from botocore.config import Config;s3=boto3.client('s3',endpoint_url=os.environ['R2_ENDPOINT'],aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],region_name='auto',config=Config(signature_version='s3v4'));s3.upload_file(r'D:\DSHgongzuoqualiyun\artifacts\qq_voice_1028\qq_20261028_0001_c97afc.mp3',os.environ['R2_BUCKET'],'assets/ref/cover_voice.mp3')"
```

### 3. 建私有仓库 + 推代码

```bash
cd /d/DSHgongzuoqualiyun/cloud/cover-cloud
git init -b main
git add -A && git commit -m "cover cloud pipeline v1"
gh repo create cover-cloud --private --source=. --push
```

### 4. 写 Secrets（Settings → Secrets and variables → Actions）

| Secret | 值 |
|---|---|
| `R2_ENDPOINT` | `https://<accountid>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | `astrobot` |
| `R2_ACCESS_KEY_ID` | R2 token 的 Access Key |
| `R2_SECRET_ACCESS_KEY` | R2 token 的 Secret |
| `R2_PUBLIC_BASE` | `https://pub-7d93b5b189f24e3094c4ed30097cc337.r2.dev` |
| `R2_CKPT_KEY`（可选） | 默认 `models/seedvc/seedvc_ckpt.tar.xz` |

一条命令批量写：

```bash
cd /d/DSHgongzuoqualiyun/cover && source ./env
repo=493764654/cover-cloud
gh secret set R2_ENDPOINT          -R $repo -b "$R2_ENDPOINT"
gh secret set R2_BUCKET            -R $repo -b "$R2_BUCKET"
gh secret set R2_ACCESS_KEY_ID     -R $repo -b "$R2_ACCESS_KEY_ID"
gh secret set R2_SECRET_ACCESS_KEY -R $repo -b "$R2_SECRET_ACCESS_KEY"
gh secret set R2_PUBLIC_BASE       -R $repo -b "$R2_PUBLIC_BASE"
```

---

## 三、日常用法

### 加歌（三种来源）

**A. 直接给链接**（YouTube / B 站 / 网易云，云端 IP 下 YouTube 不撞 bot 校验）

```bash
gh workflow run cover -R 493764654/cover-cloud \
  -f mode=cover \
  -f targets="url:https://www.youtube.com/watch?v=VPP8012DBsw" \
  -f sid=s010 -f title="男儿当自强"
```

**B. 先上传素材到 R2，再批量翻唱**

```bash
# 单曲素材进 inbox/（歌名随意，sid 自动递增）
gh workflow run cover -f mode=extract -f targets="https://youtu.be/xxx,https://youtu.be/yyy"
# 一键把 inbox/ 里所有素材全翻（无人值守）
gh workflow run cover -f mode=cover -f targets=all
```

**C. 只冒烟**

```bash
gh workflow run cover -f mode=cover -f targets="inbox/s010.wav" -f seconds=20 -f sid=s999
```

### 看进度 / 取日志

```bash
gh run list -R 493764654/cover-cloud -L 5
gh run watch -R 493764654/cover-cloud
```

### 产物与点歌插件约定

`manifest.json` 里每首歌：

```json
{
  "song_id": "s010",
  "title": "男儿当自强",
  "url": "https://pub-.../songs/s010/s010_cover.mp3",
  "variants": {
    "acapella": "https://pub-.../songs/s010/s010_acapella.mp3",
    "normal": [
      "https://pub-.../songs/s010/s010_cover.mp3",
      "https://pub-.../songs/s010/s010_cover_air.mp3"
    ]
  }
}
```

插件侧：说「清唱」播 `variants.acapella`，否则从 `variants.normal` 随机一条；老解析器只读 `url` 也能用（向后兼容）。

---

## 四、结构

```
cover-cloud/
├─ .github/workflows/cover.yml   # 手动触发：mode=cover|extract
├─ scripts/common.py             # ffmpeg / 响度 / R2 / manifest（variants 合并）
├─ scripts/pipeline.py           # 单曲全流程：取源→分离→分段换声→混音→上传
├─ scripts/extract.py            # URL → wav → R2 inbox/
├─ vendor/seedvc/                # Seed-VC 源码（已打 CPU patch，权重不进仓库）
├─ configs/                      # 推理配置（config_local.yml 副本留档）
└─ requirements.txt
```

**CPU patch**：`vendor/seedvc/inference.py` 里 whisper 原本硬编码 `torch_dtype=torch.float16`，CPU 上 matmul 直接崩，已改成 `cuda ? fp16 : fp32`。

---

## 五、排错

| 现象 | 原因 / 处理 |
|---|---|
| `从 R2 拉 Seed-VC 权重` 失败 | Secret 名或 key 不对；本地 `python -c` 先验证 R2 可读 |
| `段 N 推理失败: ... Half` | CPU patch 没生效（vendor 被上游覆盖）→ 重新打 patch |
| 单首跑 60 分钟还没完 | 降 `steps=20`、`seg=18`；或 `seconds=180` 只做副歌段 |
| 成品有断续 | `seg` 调小到 18–20 并确认 overlap 1 s |
| 月额度烧完 | 转飞桨（方案②）：同一套 `scripts/` 直接粘进 Notebook 跑，`--no-upload` 关了就能传 R2 |
| YouTube 又撞 bot | runner IP 被盯上时改 `targets` 走 B 站源，或给 yt-dlp 加 cookies（不建议放仓库） |

---

## 六、和本地 GPU 线的分工

| | 本地 GTX 1060 | 云端 Actions |
|---|---|---|
| 速度 | 一首 25 分钟（GPU 全速） | 一首 20–45 分钟（CPU） |
| 是否要开机 | 要 | 不要 |
| 适合 | 精修、试参、A/B | 挂机批量入库 |

两条线共用同一份 Seed-VC 权重与推理参数，产出物 manifest 结构一致，可以混着往 R2 里加。
