from __future__ import annotations
import numpy as np
from pathlib import Path

DEFAULT_BGE_REVISION = "7999e1d3359715c523056ef9478215996d62a620"


class BGEEmbedder:
    """Lazy CPU BGE encoder. Model files are downloaded only when explicitly indexed."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        device: str = "cpu",
        revision: str = DEFAULT_BGE_REVISION,
    ):
        self.model_name, self.device, self.revision = model_name, device, revision
        self._model = None

    @property
    def signature(self) -> str:
        return f"{self.model_name}@{self.revision}|normalized|query-instruction-v1"

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "缺少语义索引依赖。请运行 scripts/setup_models.ps1 安装模型依赖后重试。"
                ) from exc
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    device=self.device,
                    revision=self.revision,
                    cache_folder=str(
                        Path(__file__).resolve().parents[1]
                        / ".tools"
                        / "huggingface"
                    ),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"无法加载 BGE 模型 {self.model_name}。请检查网络或 Hugging Face 缓存后重试：{exc}"
                ) from exc
        return self._model

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        # BGE's published Chinese retrieval instruction, applied to queries only.
        prepared = (
            [f"为这个句子生成表示以用于检索相关文章：{t}" for t in texts]
            if is_query
            else texts
        )
        model = self._load()
        # Refuse inputs that the encoder would otherwise silently truncate.
        try:
            encoded = model.tokenizer(
                prepared, add_special_tokens=True, truncation=False, padding=False
            )
            max_len = getattr(model, "max_seq_length", 512)
            too_long = [
                i + 1
                for i, ids in enumerate(encoded["input_ids"])
                if len(ids) > max_len
            ]
            if too_long:
                raise RuntimeError(
                    f"第 {too_long[0]} 个片段超过 BGE 的 {max_len} token 上限。请缩短片段后重建索引，未保存截断向量。"
                )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"无法检查 BGE 输入长度，已停止编码以避免静默截断：{exc}"
            ) from exc
        arr = model.encode(
            prepared,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        arr = np.asarray(arr, dtype=np.float32)
        if arr.ndim != 2 or not np.isfinite(arr).all():
            raise RuntimeError("BGE 返回了无效向量，索引未保存。")
        norms = np.linalg.norm(arr, axis=1)
        if np.any(norms < 1e-8):
            raise RuntimeError("BGE 返回了零向量，索引未保存。")
        return (arr / norms[:, None]).astype(np.float32)
