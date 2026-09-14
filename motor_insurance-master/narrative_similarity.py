"""
Narrative-similarity search (Appendix C, §C.2) — compares a claim's
narrative against a reference corpus of historical claim narratives
(database.py's historical_narrative_corpus table) and surfaces the closest
matches above a confidence threshold.

Deliberately dependency-free: no scikit-learn/faiss/sentence-transformers
in requirements.txt today, and pulling one in just for a few dozen
reference narratives would be a lot of new surface area for what a plain
TF-IDF + cosine similarity implementation (stdlib only -- collections,
math) already does well at this corpus size. If the corpus grows into the
thousands, an embeddings-based approach (e.g. via Ollama) would be the
right upgrade -- this is the honest v1, not a placeholder.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.22  # cosine similarity above which a match is worth surfacing --
# tuned for a small (~20-entry), paraphrase-heavy reference corpus; revisit
# once the corpus grows large enough that false positives become the bigger risk.

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "was", "were", "at", "on", "in",
    "to", "of", "for", "with", "my", "i", "it", "this", "that", "while", "had",
    "have", "has", "be", "been", "as", "by", "from", "into", "were", "no", "not",
}


def _tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _term_freq(tokens: List[str]) -> Counter:
    return Counter(tokens)


def _cosine_similarity(tf_a: Counter, tf_b: Counter, idf: Dict[str, float]) -> float:
    common = set(tf_a) & set(tf_b)
    if not common:
        return 0.0
    dot = sum(tf_a[t] * idf.get(t, 0) * tf_b[t] * idf.get(t, 0) for t in common)
    norm_a = math.sqrt(sum((tf_a[t] * idf.get(t, 0)) ** 2 for t in tf_a))
    norm_b = math.sqrt(sum((tf_b[t] * idf.get(t, 0)) ** 2 for t in tf_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _build_idf(all_token_lists: List[List[str]]) -> Dict[str, float]:
    n_docs = len(all_token_lists)
    doc_freq: Counter = Counter()
    for tokens in all_token_lists:
        for term in set(tokens):
            doc_freq[term] += 1
    return {term: math.log((1 + n_docs) / (1 + df)) + 1 for term, df in doc_freq.items()}


@dataclass
class SimilarityMatch:
    similarity: float
    pattern_label: str
    fraud_score: int
    narrative_excerpt: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity": round(self.similarity, 3),
            "pattern_label": self.pattern_label,
            "fraud_score": self.fraud_score,
            "narrative_excerpt": self.narrative_excerpt,
        }


@dataclass
class SimilarityResult:
    matches: List[SimilarityMatch] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"matches": [m.to_dict() for m in self.matches]}

    @property
    def observation_text(self) -> str:
        if not self.matches:
            return "None"
        return "\n".join(
            f"- {m.similarity:.0%} similar to a '{m.pattern_label}' pattern "
            f"(historical fraud score {m.fraud_score}/100): \"{m.narrative_excerpt}\""
            for m in self.matches
        )


def find_similar_narratives(
    narrative_text: str,
    corpus: List[Dict[str, Any]],
    top_n: int = 3,
    threshold: float = SIMILARITY_THRESHOLD,
) -> SimilarityResult:
    result = SimilarityResult()
    if not narrative_text or not corpus:
        return result

    query_tokens = _tokenize(narrative_text)
    if not query_tokens:
        return result

    corpus_tokens = [_tokenize(entry["narrative"]) for entry in corpus]
    idf = _build_idf(corpus_tokens + [query_tokens])
    query_tf = _term_freq(query_tokens)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for entry, tokens in zip(corpus, corpus_tokens):
        sim = _cosine_similarity(query_tf, _term_freq(tokens), idf)
        if sim >= threshold:
            scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    for sim, entry in scored[:top_n]:
        narrative = entry["narrative"]
        excerpt = narrative if len(narrative) <= 140 else narrative[:137] + "..."
        result.matches.append(SimilarityMatch(
            similarity=sim,
            pattern_label=entry.get("pattern_label") or "unlabeled",
            fraud_score=entry.get("fraud_score", 0),
            narrative_excerpt=excerpt,
        ))

    return result
