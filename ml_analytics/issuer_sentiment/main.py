# main.py
import json
import logging
import time
from datetime import datetime
from typing import List, Dict

from analysis.metrics import MetricsCalculator
from config.config import SystemConfig
from processors.article_processor import ArticleProcessor
from processors.issuer_processor import IssuerProcessor
from processors.sentiment_processor import SentimentProcessor
from utils.preprocessing import batch_generator


class ArticleAnalysisSystem:
    def __init__(self, config: SystemConfig = None):
        self.config = config or SystemConfig()

        # Initialize processors
        self.issuer_processor = IssuerProcessor(self.config)
        self.sentiment_processor = SentimentProcessor(self.config)
        self.metrics_calculator = MetricsCalculator(self.config)

    def process_data(self,
                     issuers: List[Dict],
                     articles: List[Dict]) -> Dict:
        """Process all issuers and articles"""
        start_time = time.time()
        logging.info(f"Starting processing of {len(articles)} articles and {len(issuers)} issuers")

        try:
            # Process issuers first
            issuer_patterns, issuer_embeddings = self.issuer_processor.process_issuers(issuers)

            # Initialize article processor
            article_processor = ArticleProcessor(
                self.config,
                self.issuer_processor.embedding_model,
                issuer_patterns,
                self.issuer_processor.faiss_index
            )

            # Process articles in batches
            all_results = []
            batch_size = self.config.processing.article_batch_size

            for i, batch in enumerate(batch_generator(articles, batch_size)):
                # Match articles to issuers
                batch_results = article_processor.process_articles_batch(batch)

                # Add sentiment analysis
                if batch_results:
                    batch_results = self.sentiment_processor.process_matches(batch_results)
                    all_results.extend(batch_results)

                if (i + 1) % 10 == 0:
                    logging.info(f"Processed {(i + 1) * batch_size} articles...")

            # Calculate metrics
            metrics = self.metrics_calculator.calculate_metrics(all_results)

            processing_time = time.time() - start_time
            logging.info(f"Processing completed in {processing_time:.2f} seconds")

            return {
                'results': all_results,
                'metrics': metrics,
                'processing_info': {
                    'processing_time': processing_time,
                    'articles_processed': len(articles),
                    'matches_found': len(all_results),
                    'timestamp': datetime.now().isoformat()
                }
            }

        except Exception as e:
            logging.error(f"Error in main processing: {e}")
            raise


def main():
    # Example usage
    config = SystemConfig()
    system = ArticleAnalysisSystem(config)

    # Load your data
    issuers = [
        {'name': 'Apple Inc.', 'ticker': 'AAPL'},
        {'name': 'Microsoft Corporation', 'ticker': 'MSFT'}
    ]

    articles = [
        {
            'date': '2024-01-01',
            'text': 'Article about Apple and their new products...'
        }
    ]

    # Process data
    results = system.process_data(issuers, articles)

    # Save results
    with open('analysis_results.json', 'w') as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
