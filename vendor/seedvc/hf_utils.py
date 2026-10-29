import os
from huggingface_hub import hf_hub_download


LOCAL_DIRS = ["./ckpt", "./checkpoints", "."]


def _local(name):
    """优先用本地已下载的文件，避免联网鉴权失败。"""
    for d in LOCAL_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.path.getsize(cand) > 1024 * 1024:
            return cand
    return None


def load_custom_model_from_hf(repo_id, model_filename="pytorch_model.bin", config_filename=None):
    os.makedirs("./checkpoints", exist_ok=True)
    hit = _local(model_filename)
    if hit:
        if config_filename is None:
            return hit
        chit = _local(config_filename) or os.path.join("configs/presets", config_filename)
        return hit, chit
    model_path = hf_hub_download(repo_id=repo_id, filename=model_filename, cache_dir="./checkpoints")
    if config_filename is None:
        return model_path
    config_path = hf_hub_download(repo_id=repo_id, filename=config_filename, cache_dir="./checkpoints")

    return model_path, config_path