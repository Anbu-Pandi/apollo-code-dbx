import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from common.utils.logutil import setuplogger

setuplogger(logconsole=True, loglevel=logging.INFO)

from ml_analytics.industry_classifier.classifier import IndustryClassifier

COMPANIES = [
    "ARC Health OpCo LLC",
    "Avero Holdings",
    "Buildout, Inc.",
    "Maple Holdings Midco Limited",
    "Saratoga Investment Corp.",
    "SmartAC.com",
    "Stretch Zone Franchising",
    "Wellspring Worldwide Inc",
    "Paris US Midco",
    "22 HoldCo Limited",
    "25C Capital LLC",
    "41 Ultimate Topco Corporation",
    "A Place For Mom, Inc.",
    "AI Garage Door Service",
    "ABG, Inc.",
    "AAH Topco, LLC",
    "AAM Series",
    "AB Centers Acquisition Corporation",
    "Abita Brewing Co.",
    "Abracon Group Holding",
    "Albrecht, LLC",
    "Absolute Dental Group LLC",
    "Accelya US Inc.",
    "AccessOne Medicard, Inc.",
    "Accurus Aerospace Corporation",
    "Acentia Holdings",
    "ACES Intermediate",
    "ACI Group Holdings",
    "ACP Big Top Holdings",
    "ACP Falcon Buyer Inc",
    "ACP Maverick Holdings",
    "ACP Roofing Holdings",
    "ACP Tara Holdings",
    "Actus Nutrition",
    "Life Time, Inc.",
    "US Foods, Inc.",
    "Cushman & Wakefield",
    "Sabre Corporation",
    "Avis Budget Group",
    "Apple Inc.",
    "JPMorgan Chase",
    "ExxonMobil",
    "Pfizer",
    "Tesla, Inc.",
    "Walmart",
    "Goldman Sachs",
    "The Walt Disney Company",
    "NextEra Energy",
    "Price Fx Inc"
]


def main():
    clf = IndustryClassifier()

    if os.path.exists(clf.model_path):
        logging.info("Loading existing model...")
        clf.load()
    else:
        logging.info("Training new model from DBpedia + Wikipedia...")
        report = clf.fit(max_companies=3000)
        logging.info(f"\n{'='*60}")
        logging.info(f"TRAINING REPORT:\n{report}")
        logging.info(f"{'='*60}")

    logging.info(f"\nClassifying {len(COMPANIES)} companies...")
    results = clf.predict_batch(COMPANIES)

    classified = results.dropna(subset=["sector"])
    unclassified = results[results["sector"].isna()]

    logging.info(f"\n{'='*60}")
    logging.info(f"CLASSIFIED ({len(classified)}/{len(COMPANIES)}):")
    if not classified.empty:
        logging.info(f"\n{classified.to_string(index=False)}")
    logging.info(f"\nNO WIKIPEDIA PAGE ({len(unclassified)}):")
    logging.info(", ".join(unclassified["name"].tolist()))
    logging.info(f"{'='*60}")

    out_path = os.path.join(os.path.dirname(__file__), "results.csv")
    results.to_csv(out_path, index=False)
    logging.info(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
