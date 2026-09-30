# analysis/metrics.py
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class MetricsCalculator:
    def __init__(self, config):
        self.config = config

    def calculate_metrics(self, results: List[Dict]) -> Dict:
        """Calculate comprehensive metrics from results"""
        if not results:
            return {}

        # Convert to DataFrame for easier analysis
        df = self._create_analysis_df(results)

        metrics = {
            'issuer_metrics': self._calculate_issuer_metrics(df),
            'temporal_metrics': self._calculate_temporal_metrics(df),
            'correlation_metrics': self._calculate_correlation_metrics(df),
            'summary_stats': self._calculate_summary_stats(df)
        }

        return metrics

    def _create_analysis_df(self, results: List[Dict]) -> pd.DataFrame:
        """Create analysis DataFrame from results"""
        rows = []
        for article in results:
            date = pd.to_datetime(article['date'])
            for match in article['matches']:
                if 'sentiment' in match:
                    rows.append({
                        'date': date,
                        'ticker': match['ticker'],
                        'name': match['name'],
                        'relevance_score': match['relevance_score'],
                        'match_type': match['match_type'],
                        'sentiment_score': match['sentiment']['score'],
                        'sentiment_confidence': match['sentiment']['confidence'],
                        'sentiment_label': match['sentiment']['label']
                    })

        return pd.DataFrame(rows)

    def _calculate_issuer_metrics(self, df: pd.DataFrame) -> Dict:
        """Calculate metrics per issuer"""
        issuer_metrics = {}

        for ticker in df['ticker'].unique():
            issuer_df = df[df['ticker'] == ticker]

            metrics = {
                'article_count': len(issuer_df),
                'sentiment': {
                    'mean': float(issuer_df['sentiment_score'].mean()),
                    'std': float(issuer_df['sentiment_score'].std()),
                    'median': float(issuer_df['sentiment_score'].median()),
                    'weighted_mean': float(np.average(
                        issuer_df['sentiment_score'],
                        weights=issuer_df['relevance_score']
                    ))
                },
                'relevance': {
                    'mean': float(issuer_df['relevance_score'].mean()),
                    'std': float(issuer_df['relevance_score'].std())
                },
                'match_types': issuer_df['match_type'].value_counts().to_dict()
            }

            issuer_metrics[ticker] = metrics

        return issuer_metrics

    def _calculate_temporal_metrics(self, df: pd.DataFrame) -> Dict:
        """Calculate temporal metrics"""
        df['date'] = pd.to_datetime(df['date'])

        # Daily metrics
        daily = df.groupby(['date', 'ticker']).agg({
            'sentiment_score': ['mean', 'count'],
            'relevance_score': 'mean'
        }).round(4)

        # Rolling metrics
        rolling_window = self.config.sentiment.rolling_window
        rolling = df.groupby('ticker').rolling(
            window=f"{rolling_window}D",
            on='date'
        )['sentiment_score'].mean()

        return {
            'daily_metrics': daily.to_dict(),
            'rolling_metrics': rolling.to_dict()
        }

    def _calculate_correlation_metrics(self, df: pd.DataFrame) -> Dict:
        """Calculate correlation metrics"""
        correlations = {
            'sentiment_relevance': float(
                df['sentiment_score'].corr(df['relevance_score'])
            ),
            'sentiment_time': float(
                df['sentiment_score'].corr(df['date'].astype(np.int64))
            )
        }

        return correlations

    def _calculate_summary_stats(self, df: pd.DataFrame) -> Dict:
        """Calculate summary statistics"""
        return {
            'total_articles': len(df),
            'unique_issuers': len(df['ticker'].unique()),
            'date_range': {
                'start': df['date'].min().isoformat(),
                'end': df['date'].max().isoformat()
            },
            'sentiment_distribution': {
                'positive': float((df['sentiment_score'] > 0).mean()),
                'negative': float((df['sentiment_score'] < 0).mean()),
                'neutral': float((df['sentiment_score'] == 0).mean())
            }
        }