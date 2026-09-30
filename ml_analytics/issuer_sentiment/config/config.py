# config/config.py
from dataclasses import dataclass, field
from typing import Optional, Tuple
from functools import partial


def get_model_config() -> 'ModelConfig':
    return ModelConfig(
        embedding_model_name='sentence-transformers/all-MiniLM-L6-v2',
        sentiment_model_name='ProsusAI/finbert',
        max_length=512,
        batch_size=32,
        embedding_dim=384
    )


def get_processing_config() -> 'ProcessingConfig':
    return ProcessingConfig(
        article_batch_size=1000,
        similarity_threshold=0.5,
        max_matches_per_article=5,
        regex_match_boost=0.2,
        min_article_length=20,
        max_article_length=10000
    )


def get_sentiment_config() -> 'SentimentConfig':
    return SentimentConfig(
        confidence_threshold=0.3,
        neutral_range=(-0.1, 0.1),
        weighted_average=True,
        rolling_window=7
    )


@dataclass
class ModelConfig:
    embedding_model_name: str
    sentiment_model_name: str
    max_length: int
    batch_size: int
    embedding_dim: int
    cache_dir: str = '.cache'


@dataclass
class ProcessingConfig:
    article_batch_size: int
    similarity_threshold: float
    max_matches_per_article: int
    regex_match_boost: float
    min_article_length: int
    max_article_length: int


@dataclass
class SentimentConfig:
    confidence_threshold: float
    neutral_range: Tuple[float, float]
    weighted_average: bool
    rolling_window: int


@dataclass
class SystemConfig:
    model: ModelConfig = field(default_factory=get_model_config)
    processing: ProcessingConfig = field(default_factory=get_processing_config)
    sentiment: SentimentConfig = field(default_factory=get_sentiment_config)
    device: str = 'cuda'
    log_file: str = 'article_analysis.log'
    cache_dir: str = '.cache'
    debug: bool = False


# Example usage:
def create_default_config() -> SystemConfig:
    """Create a SystemConfig with default values"""
    return SystemConfig()


def create_custom_config(
        device: str = 'cuda',
        batch_size: int = 32,
        similarity_threshold: float = 0.5
) -> SystemConfig:
    """Create a SystemConfig with custom values"""
    model_config = get_model_config()
    model_config.batch_size = batch_size

    processing_config = get_processing_config()
    processing_config.similarity_threshold = similarity_threshold

    return SystemConfig(
        model=model_config,
        processing=processing_config,
        device=device
    )