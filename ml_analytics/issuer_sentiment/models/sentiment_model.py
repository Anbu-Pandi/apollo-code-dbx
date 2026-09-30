# models/sentiment_model.py
import logging
from typing import List, Dict, Optional

import numpy as np
import torch
from transformers import pipeline

from ml_analytics.issuer_sentiment.config.config import ModelConfig


class SentimentModel:
    def __init__(self, config: ModelConfig, device: str = 'cuda'):
        self.config = config
        self.device = 0 if device == 'cuda' and torch.cuda.is_available() else -1

        try:
            self.sentiment_analyzer = pipeline(
                "sentiment-analysis",
                model=config.sentiment_model_name,
                device=self.device
            )
        except Exception as e:
            logging.error(f"Error initializing sentiment model: {e}")
            raise

    def get_sentiments_batch(self, texts: List[str]) -> List[Dict[str, float]]:
        """Get sentiment scores for a batch of texts"""
        try:
            results = self.sentiment_analyzer(
                texts,
                batch_size=self.config.batch_size
            )

            processed_results = []
            for result in results:
                score = result['score']
                label = result['label']

                # Normalize to [-1, 1] range
                if label == 'negative':
                    score = -score
                elif label == 'neutral':
                    score *= 0.5

                processed_results.append({
                    'score': score,
                    'label': label,
                    'confidence': result['score']
                })

            return processed_results

        except Exception as e:
            logging.error(f"Error in sentiment analysis: {e}")
            return [{'score': 0, 'label': 'error', 'confidence': 0}] * len(texts)

    def get_aggregated_sentiment(self,
                                 scores: List[float],
                                 weights: Optional[List[float]] = None) -> Dict[str, float]:
        """Calculate aggregated sentiment metrics"""
        if not scores:
            return {
                'mean': 0,
                'std': 0,
                'median': 0,
                'weighted_mean': 0
            }

        scores_array = np.array(scores)

        if weights is not None:
            weights_array = np.array(weights)
            weighted_mean = np.average(scores_array, weights=weights_array)
        else:
            weighted_mean = np.mean(scores_array)

        return {
            'mean': float(np.mean(scores_array)),
            'std': float(np.std(scores_array)),
            'median': float(np.median(scores_array)),
            'weighted_mean': float(weighted_mean)
        }
