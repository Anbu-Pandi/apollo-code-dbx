import requests
import time

SPARQL_URL = "https://dbpedia.org/sparql"

query = """
SELECT DISTINCT ?name
  (GROUP_CONCAT(DISTINCT REPLACE(STR(?industry), "http://dbpedia.org/resource/", ""); separator="|") AS ?industries)
WHERE {
  ?company a dbo:Company .
  ?company rdfs:label ?name .
  ?company dbo:industry ?industry .
  FILTER (lang(?name) = "en")
}
GROUP BY ?name
ORDER BY ?name
OFFSET 0
LIMIT 10
"""

print("Testing SPARQL query...")
t0 = time.time()
r = requests.get(
    SPARQL_URL,
    params={"query": query, "format": "json"},
    headers={"User-Agent": "IndustryClassifier/1.0"},
    timeout=120,
)
elapsed = time.time() - t0
data = r.json()
rows = data["results"]["bindings"]
print(f"Got {len(rows)} rows in {elapsed:.1f}s")
for row in rows[:3]:
    print(f"  {row['name']['value']} -> {row['industries']['value'][:60]}")

print("\nTesting Wikipedia API...")
titles = ["Apple Inc.", "JPMorgan Chase", "ExxonMobil"]
params = {
    "action": "query",
    "titles": "|".join(titles),
    "prop": "extracts",
    "exintro": "1",
    "explaintext": "1",
    "exlimit": str(len(titles)),
    "redirects": "1",
    "format": "json",
}
r2 = requests.get("https://en.wikipedia.org/w/api.php", params=params, timeout=30)
pages = r2.json()["query"]["pages"]
for page in pages.values():
    extract = page.get("extract", "")
    print(f"  {page['title']}: {len(extract)} chars")

print("\nAll tests passed!")
