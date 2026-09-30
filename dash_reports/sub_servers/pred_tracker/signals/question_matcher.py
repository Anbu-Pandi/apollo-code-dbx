import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class MatchResult:
    is_same: bool
    confidence: float
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)


class QuestionMatcher:
    CACHE_TTL_HOURS = 1
    
    def __init__(self):
        self._cache: Dict[str, MatchResult] = {}
        self._genie = None
        self._last_refresh = datetime.now()
    
    def _get_genie(self):
        if self._genie is None:
            try:
                from genai.sql_genie.clients.custom_genie import CustomGenieClient
                self._genie = CustomGenieClient(
                    use_system_prompt=False,
                    model_deployment_name="claude-opus-4-5",
                    enable_notetaker=False,
                    enable_learning=False
                )
                self._genie._skip_system_check = True
            except Exception as e:
                logging.warning(f"QuestionMatcher: Could not init genie: {e}")
        return self._genie
    
    def _cache_key(self, q1: str, q2: str) -> str:
        combined = f"{q1.lower().strip()}|{q2.lower().strip()}"
        return hashlib.md5(combined.encode()).hexdigest()
    
    def _is_cache_valid(self, result: MatchResult) -> bool:
        age = datetime.now() - result.timestamp
        return age < timedelta(hours=self.CACHE_TTL_HOURS)
    
    def refresh_cache(self):
        now = datetime.now()
        expired_keys = [k for k, v in self._cache.items() if not self._is_cache_valid(v)]
        for k in expired_keys:
            del self._cache[k]
        self._last_refresh = now
        logging.info(f"QuestionMatcher: Cleared {len(expired_keys)} expired entries, {len(self._cache)} remain")
    
    def are_same_question(self, q1: str, q2: str) -> MatchResult:
        key = self._cache_key(q1, q2)
        reverse_key = self._cache_key(q2, q1)
        
        if key in self._cache and self._is_cache_valid(self._cache[key]):
            return self._cache[key]
        if reverse_key in self._cache and self._is_cache_valid(self._cache[reverse_key]):
            return self._cache[reverse_key]
        
        result = self._compare_with_ai(q1, q2)
        self._cache[key] = result
        return result
    
    def _compare_with_ai(self, q1: str, q2: str) -> MatchResult:
        genie = self._get_genie()
        if not genie:
            return self._fallback_compare(q1, q2)
        
        prompt = f"""Compare these two prediction market questions. Are they asking about the SAME outcome?

Question A: {q1}
Question B: {q2}

CRITICAL distinctions that make questions DIFFERENT:
- "Will X RUN for office" vs "Will X WIN office" (running ≠ winning)
- "Will X be NOMINATED" vs "Will X WIN election" (nomination ≠ election)
- "Price above $X" vs "Price above $Y" (different targets)
- "By March 2026" vs "By December 2026" (different deadlines)
- Different candidates/subjects entirely

Respond in this exact format:
SAME: true/false
CONFIDENCE: 0.0-1.0
REASON: one sentence explanation"""

        try:
            response = genie.query(prompt)
            return self._parse_ai_response(response)
        except Exception as e:
            logging.warning(f"QuestionMatcher AI error: {e}")
            return self._fallback_compare(q1, q2)
    
    def _parse_ai_response(self, response: str) -> MatchResult:
        lines = response.strip().split('\n')
        is_same = False
        confidence = 0.5
        reason = "AI comparison"
        
        for line in lines:
            line = line.strip()
            if line.upper().startswith('SAME:'):
                val = line.split(':', 1)[1].strip().lower()
                is_same = val in ['true', 'yes', '1']
            elif line.upper().startswith('CONFIDENCE:'):
                try:
                    confidence = float(line.split(':', 1)[1].strip())
                except:
                    pass
            elif line.upper().startswith('REASON:'):
                reason = line.split(':', 1)[1].strip()
        
        return MatchResult(is_same=is_same, confidence=confidence, reason=reason)
    
    def _fallback_compare(self, q1: str, q2: str) -> MatchResult:
        q1_lower, q2_lower = q1.lower(), q2.lower()
        
        running_kw = ['running', 'run for', 'will run', 'announce', 'enter race', 'declare candidacy']
        nominee_kw = ['nominee', 'nomination', 'win primary', 'win the primary', 'primary winner']
        winner_kw = ['win election', 'win the election', 'president elect', 'election winner']
        
        def get_type(q):
            if any(kw in q for kw in running_kw):
                return 'running'
            if any(kw in q for kw in nominee_kw):
                return 'nominee'
            if any(kw in q for kw in winner_kw):
                return 'winner'
            return 'general'
        
        t1, t2 = get_type(q1_lower), get_type(q2_lower)
        
        if t1 != t2 and t1 != 'general' and t2 != 'general':
            return MatchResult(
                is_same=False,
                confidence=0.8,
                reason=f"Different question types: {t1} vs {t2} (fallback)"
            )
        
        similarity = SequenceMatcher(None, q1_lower, q2_lower).ratio()
        is_same = similarity > 0.7
        
        return MatchResult(
            is_same=is_same,
            confidence=similarity,
            reason=f"SequenceMatcher: {similarity:.0%} (fallback)"
        )


_global_matcher: Optional[QuestionMatcher] = None


def get_question_matcher() -> QuestionMatcher:
    global _global_matcher
    if _global_matcher is None:
        _global_matcher = QuestionMatcher()
    return _global_matcher
