import asyncio
import os
from pathlib import Path
from typing import List, Any
from loguru import logger
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from esperanto import EmbeddingModel

from api.model_modality import validate_embedding_output


def _enabled(name: str, default: str = "false") -> bool:
    return str(os.getenv(name) or default).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_hf_model_source(model_name: str | None) -> tuple[Path | str, bool]:
    """Resolve a portable local model path; remote downloads require opt-in."""

    configured = str(
        os.getenv("HUGGINGFACE_EMBEDDING_MODEL_PATH")
        or os.getenv("VNLEGAL_LAL_MODEL_PATH")
        or ""
    ).strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError("embedding_model_path_missing")
        return path, True
    candidate = Path(str(model_name or ""))
    if str(model_name or "").strip() and candidate.is_dir():
        return candidate.resolve(), True
    if _enabled("HUGGINGFACE_EMBEDDING_ALLOW_REMOTE"):
        if not str(model_name or "").strip():
            raise RuntimeError("embedding_model_name_missing")
        return str(model_name), False
    raise RuntimeError("embedding_model_path_not_configured")

class HuggingFaceEmbeddingModel(EmbeddingModel):
    def __post_init__(self):
        super().__post_init__()
        self._tokenizer = None
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
        if self._model is not None:
            return
        
        path_to_load, local_only = resolve_hf_model_source(self.model_name)
        
        logger.info(f"Loading Hugging Face embedding model from: {path_to_load}")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                path_to_load, local_files_only=local_only
            )
            self._model = AutoModel.from_pretrained(
                path_to_load, local_files_only=local_only
            )
            self._model.eval()
            logger.info("Successfully loaded Hugging Face embedding model.")
        except Exception as e:
            logger.error(f"Failed to load Hugging Face embedding model: {e}")
            raise

    def embed(self, texts: List[str], **kwargs) -> List[List[float]]:
        if self._model is None:
            self._load()
        with torch.no_grad():
            inputs = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=2048,
                return_tensors="pt",
            )
            output = self._model(**inputs)
            # Last token pooling (just like search server does)
            last_token = inputs["attention_mask"].sum(dim=1) - 1
            embeddings = output.last_hidden_state[
                torch.arange(len(inputs["input_ids"])), last_token
            ]
            embeddings = F.normalize(embeddings, p=2, dim=1)
            return validate_embedding_output(
                embeddings.cpu().tolist(), expected_count=len(texts)
            )

    async def aembed(self, texts: List[str]) -> List[List[float]]:
        async with self._lock:
            if self._model is None:
                await asyncio.to_thread(self._load)
        return await asyncio.to_thread(self.embed, texts)

    @property
    def provider(self) -> str:
        return "huggingface"

    def _get_models(self) -> List[Any]:
        return []

    def _get_default_model(self) -> str:
        return "darklethelong/vnlegal-lal"
