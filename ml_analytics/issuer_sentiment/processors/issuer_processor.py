# processors/issuer_processor.py
import logging
import re
from typing import List, Dict, Tuple

import numpy as np

from ml_analytics.issuer_sentiment.config.config import SystemConfig
from ml_analytics.issuer_sentiment.models.embeddings import EmbeddingModel, FaissIndex
from ml_analytics.issuer_sentiment.utils.preprocessing import clean_text


class IssuerProcessor:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.embedding_model = EmbeddingModel(config.model, config.device)
        self.faiss_index = FaissIndex(config.model.embedding_dim)

    def process_issuers(self, issuers: List[Dict]) -> Tuple[Dict, np.ndarray]:
        """Process issuers and create necessary indexes"""
        try:
            # Create patterns for regex matching
            issuer_patterns = self._create_issuer_patterns(issuers)

            # Create embeddings for semantic matching
            issuer_texts = [f"{i['name']} {i['ticker']}" for i in issuers]
            issuer_embeddings = self.embedding_model.get_embeddings(issuer_texts)

            if issuer_embeddings.size > 0:
                self.faiss_index.add_embeddings(issuer_embeddings)

            return issuer_patterns, issuer_embeddings

        except Exception as e:
            logging.error(f"Error processing issuers: {e}")
            raise

    # processors/issuer_processor.py (continued)
    def _create_issuer_patterns(self, issuers: List[Dict]) -> Dict:
        """Create efficient regex patterns for issuers"""
        issuer_patterns = {}

        for issuer in issuers:
            try:
                name = clean_text(issuer['name'])
                ticker = issuer['ticker']

                # Create variations of the company name
                name_variations = self._generate_name_variations(name)

                # Create regex pattern combining all variations
                pattern_parts = [re.escape(var) for var in name_variations]
                pattern_parts.append(re.escape(ticker))

                pattern = f"({'|'.join(pattern_parts)})"
                issuer_patterns[ticker] = {
                    'pattern': re.compile(pattern, re.IGNORECASE),
                    'name': name,
                    'ticker': ticker,
                    'variations': name_variations
                }

            except Exception as e:
                logging.error(f"Error creating pattern for {issuer.get('ticker', 'unknown')}: {e}")
                continue

        return issuer_patterns

    def _generate_name_variations(self, name: str) -> List[str]:
        """Generate variations of company names"""
        variations = {name}

        # Remove common company suffixes
        basic_name = re.sub(
            r'\s+(Inc\.?|Corp\.?|Ltd\.?|LLC|Limited|Corporation|Company|Group|Holding[s]?)$',
            '',
            name,
            flags=re.IGNORECASE
        )
        variations.add(basic_name)

        # Add common abbreviations
        words = basic_name.split()
        if len(words) > 1:
            # Full acronym
            abbrev = ''.join(word[0] for word in words if word[0].isalpha())
            if len(abbrev) > 1:
                variations.add(abbrev)

            # First word + acronym of rest
            if len(words) > 2:
                rest_abbrev = ''.join(word[0] for word in words[1:] if word[0].isalpha())
                if rest_abbrev:
                    variations.add(f"{words[0]} {rest_abbrev}")

        return list(variations)
