from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict

import numpy as np
import pandas as pd

from email_parser import EmailParser
from topic_modeling import TopicModeler


@dataclass
class TopicMetrics:
    """Stores computed metrics for a topic"""
    mention_count: int  # Raw count of mentions
    prominence_score: float  # How prominent the topic is in documents
    velocity: float  # Rate of topic growth
    acceleration: float  # Change in velocity
    diversity_score: float  # How spread out across different contexts
    overlap_score: float  # Overlap with other topics
    burstiness: float  # Temporal clustering of mentions


class TopicCrowdingDetector:
    def __init__(self,
                 num_topics: int = 10,
                 time_windows: List[int] = [7, 30, 90],  # Days to analyze
                 prominence_threshold: float = 0.3,
                 overlap_threshold: float = 0.6):
        """
        Initialize the enhanced topic crowding detector.

        Args:
            num_topics: Number of topics to extract
            time_windows: List of time windows in days to analyze
            prominence_threshold: Minimum topic probability to count as prominent
            overlap_threshold: Threshold for considering topics as overlapping
        """
        self.num_topics = num_topics
        self.time_windows = time_windows
        self.prominence_threshold = prominence_threshold
        self.overlap_threshold = overlap_threshold

        self.email_parser = EmailParser()
        self.topic_modeler = TopicModeler(num_topics)

    def calculate_advanced_metrics(self,
                                   df: pd.DataFrame,
                                   doc_topics: np.ndarray,
                                   dates: List[datetime]) -> Dict[int, TopicMetrics]:
        """
        Calculate comprehensive metrics for each topic.

        Key Metrics Explained:
        1. Mention Count: Raw frequency of topic appearance
           - Significance: Basic measure of topic prevalence

        2. Prominence Score: How strongly a topic appears when present
           - Calculated using topic probabilities from LDA
           - Significance: Distinguishes between major vs minor topic mentions

        3. Velocity: Rate of change in mentions over time
           - Calculated for each time window using linear regression
           - Significance: Identifies rapidly growing topics

        4. Acceleration: Change in velocity
           - Second derivative of mention frequency
           - Significance: Identifies topics with sustained growth

        5. Diversity Score: How widely distributed a topic is
           - Uses entropy of topic distribution across different contexts
           - Significance: Distinguished focused vs broad topics

        6. Overlap Score: How much a topic overlaps with others
           - Uses Jensen-Shannon divergence between topic distributions
           - Significance: Identifies potentially redundant topics

        7. Burstiness: Temporal clustering of mentions
           - Uses coefficient of variation in inter-mention times
           - Significance: Distinguishes steady vs bursty topics
        """
        metrics = {}

        for topic_idx in range(self.num_topics):
            # Get topic probabilities for all documents
            topic_probs = doc_topics[:, topic_idx]

            # Calculate metrics for each time window
            window_metrics = []
            for window in self.time_windows:
                cutoff_date = max(dates) - timedelta(days=window)
                window_mask = [d >= cutoff_date for d in dates]

                window_probs = topic_probs[window_mask]
                window_dates = [d for d, m in zip(dates, window_mask) if m]

                if len(window_dates) > 0:
                    # Basic metrics
                    mention_count = sum(p >= self.prominence_threshold for p in window_probs)
                    prominence_score = np.mean(window_probs)

                    # Velocity and acceleration
                    velocity = self._calculate_velocity(window_dates, window_probs)
                    acceleration = self._calculate_acceleration(window_dates, window_probs)

                    # Diversity and overlap
                    diversity_score = self._calculate_diversity(window_probs)
                    overlap_score = self._calculate_overlap(topic_idx, doc_topics)

                    # Burstiness
                    burstiness = self._calculate_burstiness(window_dates, window_probs)

                    window_metrics.append(TopicMetrics(
                        mention_count=mention_count,
                        prominence_score=prominence_score,
                        velocity=velocity,
                        acceleration=acceleration,
                        diversity_score=diversity_score,
                        overlap_score=overlap_score,
                        burstiness=burstiness
                    ))

            # Aggregate metrics across time windows
            metrics[topic_idx] = self._aggregate_window_metrics(window_metrics)

        return metrics

    def _calculate_velocity(self, dates: List[datetime], probs: np.ndarray) -> float:
        """Calculate the rate of change in topic prominence."""
        days = [(d - min(dates)).days for d in dates]
        if len(days) > 1:
            try:
                slope, _ = np.polyfit(days, probs, 1)
                return slope
            except:
                pass
        return 0.0

    def _calculate_acceleration(self, dates: List[datetime], probs: np.ndarray) -> float:
        """Calculate the change in velocity (acceleration)."""
        if len(dates) < 3:
            return 0.0

        velocities = []
        window_size = max(2, len(dates) // 3)

        for i in range(len(dates) - window_size):
            window_dates = dates[i:i + window_size]
            window_probs = probs[i:i + window_size]
            velocities.append(self._calculate_velocity(window_dates, window_probs))

        if velocities:
            return np.mean(np.diff(velocities))
        return 0.0

    def _calculate_diversity(self, probs: np.ndarray) -> float:
        """Calculate topic diversity using entropy."""
        if len(probs) == 0:
            return 0.0
        # Normalize probabilities
        probs = probs / np.sum(probs)
        # Calculate entropy
        entropy = -np.sum(probs * np.log2(probs + 1e-10))
        return entropy / np.log2(len(probs))  # Normalize to [0,1]

    def _calculate_overlap(self, topic_idx: int, doc_topics: np.ndarray) -> float:
        """Calculate topic overlap using Jensen-Shannon divergence."""
        topic_dist = doc_topics[:, topic_idx]
        overlaps = []

        for other_idx in range(self.num_topics):
            if other_idx != topic_idx:
                other_dist = doc_topics[:, other_idx]
                # Calculate JS divergence
                m = 0.5 * (topic_dist + other_dist)
                js_div = 0.5 * (
                        self._kl_divergence(topic_dist, m) +
                        self._kl_divergence(other_dist, m)
                )
                overlaps.append(1 - js_div)  # Convert to similarity

        return max(overlaps) if overlaps else 0.0

    def _calculate_burstiness(self, dates: List[datetime], probs: np.ndarray) -> float:
        """Calculate topic burstiness using coefficient of variation of inter-arrival times."""
        if len(dates) < 2:
            return 0.0

        # Get timestamps of significant mentions
        mention_times = [d.timestamp() for d, p in zip(dates, probs)
                         if p >= self.prominence_threshold]

        if len(mention_times) < 2:
            return 0.0

        # Calculate inter-arrival times
        intervals = np.diff(mention_times)

        # Calculate coefficient of variation
        cv = np.std(intervals) / np.mean(intervals)
        return cv

    def _kl_divergence(self, p: np.ndarray, q: np.ndarray) -> float:
        """Calculate Kullback-Leibler divergence."""
        return np.sum(p * np.log2(p / q + 1e-10))

    def _aggregate_window_metrics(self, window_metrics: List[TopicMetrics]) -> TopicMetrics:
        """Aggregate metrics across time windows using weighted average."""
        if not window_metrics:
            return TopicMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        # Weight recent windows more heavily
        weights = np.exp(-np.arange(len(window_metrics)) / 2)
        weights = weights / np.sum(weights)

        return TopicMetrics(
            mention_count=int(np.average([m.mention_count for m in window_metrics],
                                         weights=weights)),
            prominence_score=float(np.average([m.prominence_score for m in window_metrics],
                                              weights=weights)),
            velocity=float(np.average([m.velocity for m in window_metrics],
                                      weights=weights)),
            acceleration=float(np.average([m.acceleration for m in window_metrics],
                                          weights=weights)),
            diversity_score=float(np.average([m.diversity_score for m in window_metrics],
                                             weights=weights)),
            overlap_score=float(np.average([m.overlap_score for m in window_metrics],
                                           weights=weights)),
            burstiness=float(np.average([m.burstiness for m in window_metrics],
                                        weights=weights))
        )

    def analyze(self, email_list: List[str]) -> pd.DataFrame:
        """
        Perform end-to-end analysis of topic crowding with enhanced metrics.

        Returns:
            DataFrame with comprehensive topic analysis results
        """
        # Process emails
        df = self.email_parser.process_emails(email_list)

        # Extract topics
        topics, doc_topics = self.topic_modeler.extract_topics(df['full_text'])

        # Get email dates
        dates = [self.email_parser.parse_email(email)['date'] for email in email_list]

        # Calculate enhanced metrics
        topic_metrics = self.calculate_advanced_metrics(df, doc_topics, dates)

        # Prepare results DataFrame
        results = []
        for topic in topics:
            metrics = topic_metrics[topic['id']]
            results.append({
                'topic_id': topic['id'],
                'keywords': topic['keywords'],
                'mention_count': metrics.mention_count,
                'prominence_score': metrics.prominence_score,
                'velocity': metrics.velocity,
                'acceleration': metrics.acceleration,
                'diversity_score': metrics.diversity_score,
                'overlap_score': metrics.overlap_score,
                'burstiness': metrics.burstiness,
                'crowding_score': self._calculate_crowding_score(metrics)
            })

        results_df = pd.DataFrame(results)

        # Add visualization capabilities
        from visualizer import TopicVisualizer
        viz = TopicVisualizer(results_df, dates, doc_topics)
        results_df.plot_topic_evolution = viz.plot_topic_evolution
        results_df.plot_crowding_heatmap = viz.plot_crowding_heatmap
        results_df.plot_topic_network = viz.plot_topic_network
        results_df.plot_metrics_dashboard = viz.plot_metrics_dashboard

        return results_df

    def _calculate_crowding_score(self, metrics: TopicMetrics) -> float:
        """
        Calculate overall crowding score using weighted combination of metrics.

        The crowding score indicates how "problematic" a topic is in terms of:
        - High mention frequency (raw popularity)
        - High prominence (significance when mentioned)
        - High velocity (rapid growth)
        - High acceleration (sustained growth)
        - Low diversity (too focused/narrow)
        - High overlap (redundant with other topics)
        - High burstiness (temporal clustering)
        """
        weights = {
            'mention_count': 0.15,
            'prominence_score': 0.15,
            'velocity': 0.20,
            'acceleration': 0.15,
            'diversity_score': -0.10,  # Negative because higher diversity reduces crowding
            'overlap_score': 0.15,
            'burstiness': 0.10
        }

        # Normalize metrics to [0,1] range
        normalized_metrics = {
            'mention_count': np.tanh(metrics.mention_count / 100),  # Asymptotic scaling
            'prominence_score': metrics.prominence_score,
            'velocity': np.tanh(metrics.velocity * 10),
            'acceleration': np.tanh(metrics.acceleration * 100),
            'diversity_score': metrics.diversity_score,
            'overlap_score': metrics.overlap_score,
            'burstiness': np.tanh(metrics.burstiness)
        }

        # Calculate weighted sum
        crowding_score = sum(weights[metric] * normalized_metrics[metric]
                             for metric in weights)

        # Normalize to [0,1] range
        return max(0, min(1, (crowding_score + 1) / 2))


# Example usage
if __name__ == "__main__":
    from sample_data import get_sample_emails

    # Initialize detector
    detector = TopicCrowdingDetector(
        num_topics=20,
        time_windows=[7, 30, 90],
        prominence_threshold=0.3,
        overlap_threshold=0.6
    )

    # Load and analyze emails
    email_list = get_sample_emails()
    results = detector.analyze(email_list)

    # Print detailed results
    print("\nTop Crowded Topics:")
    print(results[['keywords', 'crowding_score', 'velocity', 'diversity_score',
                   'overlap_score', 'burstiness']].sort_values('crowding_score',
                                                               ascending=False))
