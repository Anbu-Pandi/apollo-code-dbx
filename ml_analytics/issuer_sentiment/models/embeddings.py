# models/embeddings.py
import torch
from transformers import AutoTokenizer, AutoModel
import numpy as np
from typing import List, Dict, Optional
import faiss
from ml_analytics.issuer_sentiment.config.config import ModelConfig
import logging


class EmbeddingModel:
    def __init__(self, config: ModelConfig, device: str = 'cuda'):
        self.config = config
        self.device = torch.device(device if torch.cuda.is_available() else 'cpu')

        # Initialize model and tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            config.embedding_model_name,
            cache_dir=config.cache_dir
        )
        self.model = AutoModel.from_pretrained(
            config.embedding_model_name,
            cache_dir=config.cache_dir
        )
        self.model.to(self.device)

    def get_embeddings(self, texts: List[str]) -> np.ndarray:
        """Generate embeddings for a list of texts"""
        embeddings = []

        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i:i + self.config.batch_size]
            try:
                inputs = self.tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=self.config.max_length,
                    return_tensors='pt'
                )
                inputs = {k: v.to(self.device) for k, v in inputs.items()}

                with torch.no_grad():
                    outputs = self.model(**inputs)
                    batch_embeddings = outputs.last_hidden_state[:, 0].cpu().numpy()
                    embeddings.append(batch_embeddings)

            except Exception as e:
                logging.error(f"Error generating embeddings for batch: {e}")
                continue

        return np.vstack(embeddings) if embeddings else np.array([])


class FaissIndex:
    def __init__(self, embedding_dim: int, index_type: str = 'flat'):
        if index_type == 'flat':
            self.index = faiss.IndexFlatIP(embedding_dim)
        elif index_type == 'ivf':
            quantizer = faiss.IndexFlatIP(embedding_dim)
            self.index = faiss.IndexIVFFlat(quantizer, embedding_dim, 100)
        else:
            raise ValueError(f"Unsupported index type: {index_type}")

        self.index_type = index_type

    def add_embeddings(self, embeddings: np.ndarray):
        """Add embeddings to the FAISS index"""
        if embeddings.size == 0:
            return

        normalized_embeddings = embeddings.copy()
        faiss.normalize_L2(normalized_embeddings)

        if self.index_type == 'ivf' and not self.index.is_trained:
            self.index.train(normalized_embeddings)

        self.index.add(normalized_embeddings)

    def search(self, query_embeddings: np.ndarray, k: int) -> tuple:
        """Search for similar embeddings"""
        if query_embeddings.size == 0:
            return np.array([]), np.array([])

        normalized_queries = query_embeddings.copy()
        faiss.normalize_L2(normalized_queries)
        return self.index.search(normalized_queries, k)