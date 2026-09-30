import logging
from typing import List, Dict, Tuple, Union

import numpy as np
import spacy
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer


class TopicModeler:
    def __init__(self, num_topics: int = 10):
        """
        Initialize the TopicModeler with spaCy.

        Args:
            num_topics: Number of topics to extract
        """
        self.num_topics = num_topics
        try:
            # Load the small English model
            try:
                self.nlp = spacy.load("en_core_web_lg")
            except OSError:
                logging.info("Downloading spaCy model...")
                spacy.cli.download("en_core_web_sm")
                self.nlp = spacy.load("en_core_web_sm")

            # Disable unnecessary pipeline components for better performance
            self.nlp.disable_pipes(["ner", "parser"])

        except Exception as e:
            logging.error(f"Error initializing spaCy: {str(e)}")
            raise

        # Initialize vectorizer
        self.vectorizer = CountVectorizer(
            max_features=5000,
            ngram_range=(1, 3),
            stop_words='english'
        )

    def preprocess_text(self, text: str) -> str:
        """
        Clean and preprocess text using spaCy.

        Args:
            text: Input text to preprocess

        Returns:
            Preprocessed text string
        """
        # Process with spaCy
        doc = self.nlp(text.lower())

        # Filter tokens and lemmatize
        tokens = []
        for token in doc:
            # Only include tokens that meet our criteria
            if (len(token.text) > 2 and  # Length check
                    not token.is_punct and  # Not punctuation
                    not token.is_space and  # Not whitespace
                    not token.like_num and  # Not a number
                    not token.is_stop and  # Not a stopword
                    token.text not in self.vectorizer.stop_words):  # Additional stopwords check

                # Use token.lemma_ for lemmatization
                lemma = token.lemma_.lower().strip()
                if lemma:  # Only add non-empty strings
                    tokens.append(lemma)

        return ' '.join(tokens)

    def extract_topics(
            self,
            texts: List[str]
    ) -> Tuple[List[Dict[str, Union[int, List[str]]]], np.ndarray]:
        """
        Extract topics using LDA.

        Args:
            texts: List of input texts to analyze

        Returns:
            Tuple containing:
            - List of topic dictionaries with keywords
            - Document-topic matrix

        Raises:
            Exception: If topic extraction fails
        """
        try:
            # Preprocess all texts
            processed_texts = [self.preprocess_text(text) for text in texts]

            # Create document-term matrix
            dtm = self.vectorizer.fit_transform(processed_texts)

            # Initialize and fit LDA
            lda = LatentDirichletAllocation(
                n_components=self.num_topics,
                random_state=42,
                learning_method='online'
            )

            # Get document-topic distributions
            doc_topics = lda.fit_transform(dtm)

            # Get feature names for topic keywords
            feature_names = self.vectorizer.get_feature_names_out()

            # Extract top words for each topic
            topics = []
            for topic_idx, topic in enumerate(lda.components_):
                top_word_indices = topic.argsort()[:-5 - 1:-1]
                top_words = [feature_names[i] for i in top_word_indices]
                topics.append({
                    'id': topic_idx,
                    'keywords': top_words
                })

            return topics, doc_topics

        except Exception as e:
            logging.error(f"Error in topic extraction: {str(e)}")
            raise

    def get_dominant_topic(
            self,
            doc_topics: np.ndarray,
            doc_index: int
    ) -> Tuple[int, float]:
        """
        Get the dominant topic for a specific document.

        Args:
            doc_topics: Document-topic matrix
            doc_index: Index of the document

        Returns:
            Tuple of (topic_id, topic_probability)
        """
        topic_distribution = doc_topics[doc_index]
        dominant_topic = int(np.argmax(topic_distribution))
        topic_probability = float(topic_distribution[dominant_topic])
        return dominant_topic, topic_probability


if __name__ == '__main__':
    # Initialize and use the topic modeler
    modeler = TopicModeler(num_topics=5)
    texts = [
        "Machine learning is transforming the tech industry",
        "Climate change affects global weather patterns",
        "New advances in artificial intelligence research"
    ]

    # Extract topics
    topics, doc_topics = modeler.extract_topics(texts)

    # Print topics
    for topic in topics:
        print(f"Topic {topic['id']}: {', '.join(topic['keywords'])}")

    # Get dominant topic for first document
    topic_id, probability = modeler.get_dominant_topic(doc_topics, 0)
    print(f"Document 0 belongs to topic {topic_id} with probability {probability:.2f}")
