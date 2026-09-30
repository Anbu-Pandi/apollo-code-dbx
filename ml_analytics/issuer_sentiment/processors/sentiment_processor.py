# processors/sentiment_processor.py
import logging
from typing import List, Dict

from ml_analytics.issuer_sentiment.config.config import SystemConfig
from ml_analytics.issuer_sentiment.models.sentiment_model import SentimentModel


class SentimentProcessor:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.sentiment_model = SentimentModel(config.model, config.device)

    def process_matches(self, matched_articles: List[Dict]) -> List[Dict]:
        """Process matched articles to add sentiment scores"""
        if not matched_articles:
            return []

        try:
            # Extract texts for batch processing
            texts = [article['cleaned_text'] for article in matched_articles]

            # Get sentiment scores
            sentiment_results = self.sentiment_model.get_sentiments_batch(texts)

            # Add sentiment scores to results
            results_with_sentiment = []
            for article, sentiment in zip(matched_articles, sentiment_results):
                if sentiment['confidence'] >= self.config.sentiment.confidence_threshold:
                    article_with_sentiment = article.copy()

                    # Add sentiment information to each match
                    for match in article_with_sentiment['matches']:
                        match['sentiment'] = {
                            'score': sentiment['score'],
                            'label': sentiment['label'],
                            'confidence': sentiment['confidence']
                        }

                    results_with_sentiment.append(article_with_sentiment)

            return results_with_sentiment

        except Exception as e:
            logging.error(f"Error processing sentiments: {e}")
            return matched_articles
