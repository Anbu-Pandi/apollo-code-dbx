import logging
import numpy as np
import pandas as pd
from typing import Dict, Tuple, List, Any

from ml_analytics.ensembler.predictors.regime import MarketRegimeDetector


class RegimeManager:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.regime_detectors = {}
        self.global_detector = MarketRegimeDetector(window_size=window_size)

    def get_or_create_detector(self, group_id):
        if group_id not in self.regime_detectors:
            self.regime_detectors[group_id] = MarketRegimeDetector(
                window_size=self.window_size,
                group_id=group_id
            )
        return self.regime_detectors[group_id]

    def detect_regime(self, returns_df, group_id=None):
        try:
            detector = self.get_or_create_detector(group_id) if group_id else self.global_detector

            regime_features = detector.prepare_regime_features(returns_df)

            if not detector.is_fitted and len(regime_features) > 0:
                detector.fit(regime_features)

            _, regime_label, regime_chars = detector.predict_regime(regime_features)

            return regime_label, regime_chars

        except Exception as e:
            logging.error(f"Error detecting regime: {str(e)}")
            return "Normal", {}

    def train_detectors(self, data_by_groups):
        try:
            # Train global detector with all data combined
            all_data = pd.concat(list(data_by_groups.values())) if data_by_groups else pd.DataFrame()

            if not all_data.empty:
                global_features = self.global_detector.prepare_regime_features(all_data)
                if len(global_features) > 0:
                    self.global_detector.fit(global_features)

            # Train group-specific detectors
            for group_id, group_data in data_by_groups.items():
                if not group_data.empty:
                    detector = self.get_or_create_detector(group_id)
                    features = detector.prepare_regime_features(group_data)
                    if len(features) > 0:
                        detector.fit(features)

            return True
        except Exception as e:
            logging.error(f"Error training regime detectors: {str(e)}")
            return False

    def process_regimes(self, data_by_groups):
        regimes_by_group = {}

        for group_id, group_data in data_by_groups.items():
            regime, chars = self.detect_regime(group_data, group_id)
            regimes_by_group[group_id] = {
                'regime': regime,
                'characteristics': chars
            }

        return regimes_by_group

    def get_dominant_regime(self, regimes_by_group):
        from collections import Counter

        # Extract just the regime labels
        regime_labels = [info['regime'] for info in regimes_by_group.values()]

        # Count occurrences
        if not regime_labels:
            return "Normal", {}

        counter = Counter(regime_labels)
        dominant_regime = counter.most_common(1)[0][0]

        # Get average characteristics
        all_chars = {}
        for info in regimes_by_group.values():
            chars = info.get('characteristics', {})
            for key, value in chars.items():
                if key not in all_chars:
                    all_chars[key] = []
                all_chars[key].append(value)

        avg_chars = {k: np.mean(v) for k, v in all_chars.items()}

        return dominant_regime, avg_chars