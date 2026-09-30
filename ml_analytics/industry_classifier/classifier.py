import logging
import os
from typing import List, Optional, Union

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from ml_analytics.industry_classifier.data_fetcher import fetch_training_data, fetch_wiki_descriptions
from ml_analytics.industry_classifier.gics import map_industries_bulk


DEFAULT_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.joblib")


class IndustryClassifier:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        self.model_path = model_path
        self.pipeline: Optional[Pipeline] = None
        self.classes_: Optional[np.ndarray] = None

    def build_training_set(self, max_companies: int = 3000) -> pd.DataFrame:
        df = fetch_training_data(max_companies)
        df["gics_sector"] = map_industries_bulk(df["industry_raw"].tolist())
        df = df.dropna(subset=["gics_sector", "description"])
        df = df.drop_duplicates(subset=["name"])
        logging.info(f"Training set: {len(df)} labeled companies across {df['gics_sector'].nunique()} sectors")
        logging.info(f"Sector distribution:\n{df['gics_sector'].value_counts().to_string()}")
        return df

    def fit(self, df: Optional[pd.DataFrame] = None, test_size: float = 0.2, max_companies: int = 3000):
        if df is None:
            df = self.build_training_set(max_companies)

        X, y = df["description"].values, df["gics_sector"].values

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, stratify=y, random_state=42
        )

        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(
                ngram_range=(1, 2),
                max_features=10000,
                stop_words="english",
                sublinear_tf=True,
            )),
            ("svm", LinearSVC(
                C=1.0,
                max_iter=5000,
                class_weight="balanced",
            )),
        ])

        logging.info(f"Training on {len(X_train)} samples, testing on {len(X_test)}")
        self.pipeline.fit(X_train, y_train)
        self.classes_ = self.pipeline.classes_

        y_pred = self.pipeline.predict(X_test)
        report = classification_report(y_test, y_pred)
        logging.info(f"Classification Report:\n{report}")

        self.save()
        return report

    def predict(self, descriptions: Union[str, List[str]]) -> Union[str, List[str]]:
        if self.pipeline is None:
            self.load()

        single = isinstance(descriptions, str)
        if single:
            descriptions = [descriptions]

        predictions = self.pipeline.predict(descriptions).tolist()
        return predictions[0] if single else predictions

    def predict_proba_approx(self, descriptions: Union[str, List[str]]) -> pd.DataFrame:
        if self.pipeline is None:
            self.load()

        single = isinstance(descriptions, str)
        if single:
            descriptions = [descriptions]

        decision = self.pipeline.decision_function(descriptions)
        if decision.ndim == 1:
            decision = decision.reshape(1, -1)

        exp_scores = np.exp(decision - decision.max(axis=1, keepdims=True))
        proba = exp_scores / exp_scores.sum(axis=1, keepdims=True)
        return pd.DataFrame(proba, columns=self.pipeline.classes_)

    def predict_batch(self, company_names: List[str], fallback_to_name: bool = True) -> pd.DataFrame:
        if self.pipeline is None:
            self.load()

        wiki_df = fetch_wiki_descriptions(company_names)
        found = wiki_df.dropna(subset=["description"])
        found = found[found["description"].str.len() > 50]

        all_names = pd.DataFrame({"name": company_names})
        all_names["sector"] = None
        all_names["confidence"] = None
        all_names["source"] = None

        if not found.empty:
            sectors = self.predict(found["description"].tolist())
            proba = self.predict_proba_approx(found["description"].tolist())
            wiki_result = found[["name"]].copy()
            wiki_result["sector"] = sectors
            wiki_result["confidence"] = proba.max(axis=1).values.round(3)
            wiki_result["source"] = "wikipedia"
            all_names = all_names.drop(columns=["sector", "confidence", "source"])
            all_names = all_names.merge(wiki_result, on="name", how="left")
            logging.info(f"Wikipedia descriptions classified: {len(wiki_result)}/{len(company_names)}")

        if fallback_to_name:
            missing_mask = all_names["sector"].isna()
            missing_names = all_names.loc[missing_mask, "name"].tolist()
            if missing_names:
                name_sectors = self.predict(missing_names)
                name_proba = self.predict_proba_approx(missing_names)
                all_names.loc[missing_mask, "sector"] = name_sectors
                all_names.loc[missing_mask, "confidence"] = name_proba.max(axis=1).values.round(3)
                all_names.loc[missing_mask, "source"] = "name"
                logging.info(f"Name-based fallback classified: {len(missing_names)} more companies")

        return all_names

    def save(self, path: str = None):
        path = path or self.model_path
        joblib.dump(self.pipeline, path)
        logging.info(f"Model saved to {path}")

    def load(self, path: str = None):
        path = path or self.model_path
        self.pipeline = joblib.load(path)
        self.classes_ = self.pipeline.classes_
        logging.info(f"Model loaded from {path}")
