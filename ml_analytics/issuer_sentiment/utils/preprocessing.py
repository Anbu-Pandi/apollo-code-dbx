# utils/preprocessing.py
import re
from typing import List, Dict

from ml_analytics.issuer_sentiment.config.config import ProcessingConfig


def clean_text(text: str) -> str:
    """Clean and normalize text"""
    if not isinstance(text, str):
        return ""

    # Remove URLs
    text = re.sub(r'http[s]?://\S+', '', text)

    # Remove special characters but keep periods in abbreviations
    text = re.sub(r'[^\w\s\.]', ' ', text)

    # Normalize whitespace
    text = ' '.join(text.split())

    return text


def is_valid_article(article: Dict, config: ProcessingConfig) -> bool:
    """Check if article is valid for processing"""
    if not article.get('text') or not article.get('date'):
        return False

    text_length = len(article['text'])
    return config.min_article_length <= text_length <= config.max_article_length


def batch_generator(items: List, batch_size: int):
    """Generate batches from a list"""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]
