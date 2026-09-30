import logging
import re
import warnings
from typing import List, Optional, Set, Tuple, Union

import numpy as np
import pandas as pd
from ftfy import fix_text
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors


class TextMatcher:
    def __init__(
        self,
        train_list: List[str],
        n: int = 3,
        remove_words: Optional[Union[List[str], Set[str]]] = None,
        case_sensitive: bool = False,
        min_token_length: int = 2,
        **kwargs
    ):
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        
        self.train_list = train_list
        self.n = n
        self.remove_words = set(remove_words) if remove_words else set()
        self.case_sensitive = case_sensitive
        self.min_token_length = min_token_length
        self.kwargs = kwargs
        
        self.vectorizer = TfidfVectorizer(
            min_df=1,
            analyzer=self._ngrams_transform,
            lowercase=not case_sensitive
        )
        self.nbrs_model = None
        
        self.chars_to_remove = re.compile(r'[\(\)\.\|\[\]\{\}\'"]')
        self.multiple_spaces = re.compile(r'\s+')
        self.special_chars = re.compile(r'[,\-\.\/]\sBD')

    def _clean_text(self, text: str) -> str:
        text = str(text)
        text = fix_text(text)
        text = text.encode("ascii", errors="ignore").decode()
        
        if not self.case_sensitive:
            text = text.lower()
        
        text = self.chars_to_remove.sub('', text)
        text = text.replace('&', 'and')
        text = text.replace(',', ' ')
        text = text.replace('-', ' ')
        
        if self.remove_words:
            words = text.split()
            words = [w for w in words 
                    if w.lower() not in {word.lower() for word in self.remove_words}
                    and len(w) >= self.min_token_length]
            text = ' '.join(words)
        
        if not self.case_sensitive:
            text = text.title()
        
        text = self.multiple_spaces.sub(' ', text).strip()
        text = f' {text} '
        text = self.special_chars.sub('', text)
        
        return text

    def _ngrams_transform(self, text: str) -> List[str]:
        text = self._clean_text(text)
        ngrams = zip(*[text[i:] for i in range(self.n)])
        return [''.join(ngram) for ngram in ngrams]

    def tfidf_fit(self) -> None:
        logging.info('Vectorizing the data - this could take a few minutes for large datasets')
        self.tfidf = self.vectorizer.fit_transform(self.train_list)
        
        logging.info('Fitting nearest neighbors model')
        self.nbrs_model = NearestNeighbors(
            n_neighbors=1,
            n_jobs=-1,
            **self.kwargs
        ).fit(self.tfidf)
        
        logging.info('Model fitting completed')

    def get_matches(
        self,
        query: Union[str, List[str]],
        threshold: float = 99.0
    ) -> pd.DataFrame:
        if self.nbrs_model is None:
            raise ValueError("Model not fit yet. Call tfidf_fit() first.")

        if isinstance(query, str):
            query = [query]
        
        query = list(dict.fromkeys(query))

        logging.info('Finding nearest neighbors')
        tfidf_query = self.vectorizer.transform(query)
        distances, indices = self.nbrs_model.kneighbors(tfidf_query)

        logging.info('Processing matches')
        matches = []
        for i, (dist, idx) in enumerate(zip(distances, indices)):
            if dist[0] <= threshold:
                matches.append([
                    dist[0],
                    self.train_list[idx[0]],
                    query[i]
                ])

        logging.info('Matching completed')
        matches_df = pd.DataFrame(
            matches,
            columns=['match_score', 'matched_text', 'text']
        )
        
        return matches_df.sort_values(
            'match_score',
            ascending=False
        )[['text', 'matched_text', 'match_score']]

    def get_topk_matches(
        self,
        query: Union[str, List[str]],
        k: int = 5,
        batch_size: int = 100,
    ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        if isinstance(query, str):
            query = [query]
        query = list(dict.fromkeys(query))

        logging.info(f"Transforming {len(query)} queries")
        query_tfidf = self.vectorizer.transform(query)
        n = query_tfidf.shape[0]
        actual_k = min(k, self.tfidf.shape[0])

        all_distances = np.empty((n, actual_k), dtype=np.float64)
        all_indices = np.empty((n, actual_k), dtype=np.int64)

        n_batches = (n + batch_size - 1) // batch_size
        logging.info(f"Finding top-{actual_k} in {n_batches} batches (size {batch_size})")

        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            sim = query_tfidf[start:end].dot(self.tfidf.T).tocsr()

            for i in range(sim.shape[0]):
                row = sim.getrow(i)
                data, col_idx = row.data, row.indices

                if len(data) <= actual_k:
                    order = np.argsort(-data)
                    pad = actual_k - len(data)
                    top_sim = np.concatenate([data[order], np.zeros(pad)])
                    top_idx = np.concatenate([col_idx[order], np.zeros(pad, dtype=np.int64)])
                else:
                    topk_pos = np.argpartition(-data, actual_k)[:actual_k]
                    order = np.argsort(-data[topk_pos])
                    topk_pos = topk_pos[order]
                    top_sim = data[topk_pos]
                    top_idx = col_idx[topk_pos]

                all_distances[start + i] = 1.0 - top_sim
                all_indices[start + i] = top_idx

            if (start // batch_size) % 20 == 0:
                logging.info(f"  batch {start // batch_size + 1}/{n_batches}")

        logging.info("Top-k matching completed")
        return all_distances, all_indices, query


if __name__ == '__main__':
    train_list = ['Apple Inc.', 'Microsoft Corporation', 'Amazon.com Inc.', 'Alphabet Inc.', 'Facebook Inc.']
    m = TextMatcher(train_list)
    m.tfidf_fit()
    out = m.get_matches(['Apple', 'Microsoft', 'Amazon', 'Alphabet', 'Facebook'])
    print(out)