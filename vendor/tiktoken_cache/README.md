# tiktoken BPE 离线缓存

tiktoken 默认从 `openaipublic.blob.core.windows.net` 下载 BPE 词表。公司内网 / 离线
机器拦了这个域名时，下载会超时，`get_encoding()` 抛异常——上下文 breakdown 视化退化。

把官方 BPE 文件直接签入仓库，启动时通过 `TIKTOKEN_CACHE_DIR` 让 tiktoken 从本地路径
加载，零网络依赖。

## 文件来源

文件名是 tiktoken 缓存约定（URL 的 sha1）：

| 文件名 | 对应 encoding | 适用模型 |
|---|---|---|
| `9b5ad71b2ce5302211f9c61530b329a4922fc6a4` | cl100k_base | GPT-3.5 / GPT-4 系列；多数非 OpenAI 模型也用这个 fallback |
| `fb374d419588a4632f3f557e76b4b70aebbca790` | o200k_base  | GPT-4o 系列 |

下载源：
- https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
- https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken

## 启动接入

`main.py` 顶部设环境变量后再 import 任何用 tiktoken 的模块：

```python
import os, pathlib
os.environ.setdefault(
    "TIKTOKEN_CACHE_DIR",
    str(pathlib.Path(__file__).parent / "vendor" / "tiktoken_cache"),
)
```

## 升级

OpenAI 偶尔更新 BPE 词表（很少；近年 cl100k_base 没变过）。要升级时：

```bash
# 在能联网的机器上跑：
.venv/Scripts/python.exe -c "
import tiktoken
tiktoken.get_encoding('cl100k_base').encode('warmup')
tiktoken.get_encoding('o200k_base').encode('warmup')
"
# 然后从 %TMP%/data-gym-cache/ （Windows）或 ~/.cache/data-gym-cache/ （Linux）
# 拷文件覆盖此目录下的同名文件。
```
