# processors/article_processor.py
import logging
from collections import defaultdict
from typing import List, Dict

import numpy as np
import pandas as pd

from ml_analytics.issuer_sentiment.config.config import SystemConfig
from ml_analytics.issuer_sentiment.models.embeddings import EmbeddingModel
from ml_analytics.issuer_sentiment.utils.preprocessing import clean_text, is_valid_article


class ArticleProcessor:
    def __init__(self,
                 config: SystemConfig,
                 embedding_model: EmbeddingModel,
                 issuer_patterns: Dict,
                 faiss_index):
        self.config = config
        self.embedding_model = embedding_model
        self.issuer_patterns = issuer_patterns
        self.faiss_index = faiss_index

    def process_articles_batch(self, articles: List[Dict]) -> List[Dict]:
        """Process a batch of articles"""
        # Filter invalid articles
        valid_articles = [
            article for article in articles
            if is_valid_article(article, self.config.processing)
        ]

        if not valid_articles:
            return []

        df = pd.DataFrame(valid_articles)

        # Clean article text
        df['cleaned_text'] = df['text'].apply(clean_text)

        # 1. Initial regex matching
        matches = self._regex_match_batch(df)

        # 2. Semantic matching for articles with potential matches
        matched_indices = list(matches.keys())
        if not matched_indices:
            return []

        results = self._semantic_match_batch(df, matched_indices, matches)
        return results

    def _regex_match_batch(self, df: pd.DataFrame) -> Dict:
        """Perform regex matching on batch"""
        matches = defaultdict(list)

        for ticker, issuer_info in self.issuer_patterns.items():
            try:
                mask = df['cleaned_text'].str.contains(
                    issuer_info['pattern'],
                    regex=True,
                    na=False
                )
                matching_indices = df.index[mask].tolist()

                for idx in matching_indices:
                    matches[idx].append(ticker)

            except Exception as e:
                logging.error(f"Error in regex matching for {ticker}: {e}")
                continue

        return matches

    def _semantic_match_batch(self,
                              df: pd.DataFrame,
                              matched_indices: List[int],
                              regex_matches: Dict) -> List[Dict]:
        """Perform semantic matching on regex matches"""
        try:
            matched_texts = df.loc[matched_indices, 'cleaned_text'].tolist()
            article_embeddings = self.embedding_model.get_embeddings(matched_texts)

            if article_embeddings.size == 0:
                return []

            # Get similar issuers using FAISS
            D, I = self.faiss_index.search(
                article_embeddings,
                self.config.processing.max_matches_per_article
            )

            results = []
            for i, (idx, tickers) in enumerate(regex_matches.items()):
                article = df.loc[idx].to_dict()
                article_matches = self._combine_matches(
                    tickers,
                    D[i],
                    I[i],
                    self.config.processing.similarity_threshold
                )

                if article_matches:
                    results.append({
                        'date': article['date'],
                        'text': article['text'],
                        'cleaned_text': article['cleaned_text'],
                        'matches': article_matches
                    })

            return results

        except Exception as e:
            logging.error(f"Error in semantic matching: {e}")
            return []

    def _combine_matches(self,
                         regex_tickers: List[str],
                         similarities: np.ndarray,
                         indices: np.ndarray,
                         threshold: float) -> List[Dict]:
        """Combine regex and semantic matches"""
        matches = []
        seen_tickers = set()

        # Add regex matches first
        for ticker in regex_tickers:
            if ticker not in seen_tickers:
                matches.append({
                    'ticker': ticker,
                    'name': self.issuer_patterns[ticker]['name'],
                    'relevance_score': 1.0 + self.config.processing.regex_match_boost,
                    'match_type': 'regex'
                })
                seen_tickers.add(ticker)

        # Add semantic matches
        for sim_score, issuer_idx in zip(similarities, indices):
            if sim_score > threshold and len(matches) < self.config.processing.max_matches_per_article:
                ticker = list(self.issuer_patterns.keys())[issuer_idx]
                if ticker not in seen_tickers:
                    matches.append({
                        'ticker': ticker,
                        'name': self.issuer_patterns[ticker]['name'],
                        'relevance_score': float(sim_score),
                        'match_type': 'semantic'
                    })
                    seen_tickers.add(ticker)

        return sorted(matches, key=lambda x: x['relevance_score'], reverse=True)
