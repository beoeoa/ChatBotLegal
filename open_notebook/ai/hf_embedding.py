import asyncio
import os
from typing import List, Any
from loguru import logger
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from esperanto import EmbeddingModel

class HuggingFaceEmbeddingModel(EmbeddingModel):
    def __post_init__(self):
        super().__post_init__()
        self._tokenizer = None
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self):
        if self._model is not None:
            return
        
        # Local model cache path
        local_path = r"D:\legal-chatbot-data\sentence_transformers\models--darklethelong--vnlegal-lal\snapshots\de759324ef931a2475ae8db97137b6a6cbb98aa0"
        path_to_load = local_path if os.path.exists(local_path) else self.model_name
        
        logger.info(f"Loading Hugging Face embedding model from: {path_to_load}")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(path_to_load)
            self._model = AutoModel.from_pretrained(path_to_load)
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
            return embeddings.cpu().tolist()

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
