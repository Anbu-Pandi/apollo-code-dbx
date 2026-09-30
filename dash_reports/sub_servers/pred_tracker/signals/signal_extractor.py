import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from enum import Enum
from difflib import SequenceMatcher


class SignalType(Enum):
    ARBITRAGE = "arbitrage"
    MISPRICING = "mispricing"
    WHALE_FOLLOW = "whale_follow"
    CONTRARIAN = "contrarian"
    MOMENTUM = "momentum"


@dataclass
class MathTrace:
    formula_name: str
    formula_latex: str
    inputs: Dict[str, float]
    calculation: str
    result: float
    
    def to_dict(self) -> dict:
        return {
            "formula": self.formula_name,
            "latex": self.formula_latex,
            "inputs": {k: round(v, 4) if isinstance(v, float) else v for k, v in self.inputs.items()},
            "calc": self.calculation,
            "result": round(self.result, 4)
        }


@dataclass
class Signal:
    id: str
    signal_type: SignalType
    title: str
    description: str
    ev_percent: float
    kelly_fraction: float
    confidence: float
    poly_url: Optional[str] = None
    kalshi_url: Optional[str] = None
    poly_price: Optional[float] = None
    kalshi_price: Optional[float] = None
    model_price: Optional[float] = None
    spread: Optional[float] = None
    category: str = "GENERAL"
    timestamp: datetime = field(default_factory=datetime.now)
    news_citations: List[str] = field(default_factory=list)
    math_traces: List[MathTrace] = field(default_factory=list)
    rank: int = 0
    volume: float = 0
    liquidity: float = 0
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.signal_type.value,
            "title": self.title,
            "description": self.description,
            "ev_percent": round(self.ev_percent, 1),
            "kelly_fraction": round(self.kelly_fraction, 3),
            "kelly_percent": round(self.kelly_fraction * 100, 1),
            "confidence": round(self.confidence, 2),
            "poly_url": self.poly_url,
            "kalshi_url": self.kalshi_url,
            "poly_price": self.poly_price,
            "kalshi_price": self.kalshi_price,
            "model_price": self.model_price,
            "spread": round(self.spread, 1) if self.spread else None,
            "category": self.category,
            "timestamp": self.timestamp.isoformat(),
            "news_citations": self.news_citations,
            "math": [t.to_dict() for t in self.math_traces],
            "rank": self.rank,
            "volume": self.volume,
            "liquidity": self.liquidity
        }


class SignalExtractor:
    MIN_ARB_SPREAD = 0.08
    MIN_EV_THRESHOLD = 0.05
    KELLY_FRACTION = 0.25
    
    def __init__(self):
        self._signal_counter = 0
        self._question_matcher = None
    
    def _get_matcher(self):
        if self._question_matcher is None:
            from dash_reports.sub_servers.pred_tracker.signals.question_matcher import get_question_matcher
            self._question_matcher = get_question_matcher()
        return self._question_matcher
    
    def _next_id(self) -> str:
        self._signal_counter += 1
        return f"sig_{self._signal_counter:04d}"
    
    def calculate_ev(self, model_prob: float, market_price: float) -> tuple:
        if market_price <= 0 or market_price >= 1:
            return 0, None
        
        edge = model_prob - market_price
        payout_mult = 1 / market_price
        ev = edge * payout_mult
        
        trace = MathTrace(
            formula_name="Expected Value",
            formula_latex="EV = (p_model - p_market) × (1 / p_market)",
            inputs={"p_model": model_prob, "p_market": market_price},
            calculation=f"({model_prob:.2f} - {market_price:.2f}) × (1 / {market_price:.2f}) = {edge:.3f} × {payout_mult:.2f}",
            result=ev
        )
        return ev, trace
    
    def calculate_kelly(self, prob: float, odds: float, fraction: float = None) -> tuple:
        if fraction is None:
            fraction = self.KELLY_FRACTION
        if odds <= 0:
            return 0, None
        
        q = 1 - prob
        full_kelly = (prob * odds - q) / odds
        adj_kelly = max(full_kelly * fraction, 0)
        
        trace = MathTrace(
            formula_name="Kelly Criterion",
            formula_latex="f* = (p × b - q) / b × fraction",
            inputs={"p": prob, "q": q, "b (odds)": odds, "fraction": fraction},
            calculation=f"({prob:.2f} × {odds:.2f} - {q:.2f}) / {odds:.2f} × {fraction} = {full_kelly:.3f} × {fraction} = {adj_kelly:.3f}",
            result=adj_kelly
        )
        return adj_kelly, trace
    
    def calculate_spread(self, price1: float, price2: float) -> tuple:
        spread = abs(price1 - price2)
        
        trace = MathTrace(
            formula_name="Arbitrage Spread",
            formula_latex="spread = |p₁ - p₂|",
            inputs={"p_poly": price1, "p_kalshi": price2},
            calculation=f"|{price1:.2f} - {price2:.2f}| = {spread:.3f}",
            result=spread
        )
        return spread, trace
    
    def detect_arbitrage(self, poly_markets: List, kalshi_markets: List) -> List[Signal]:
        candidates = []
        seen_questions = set()
        
        poly_by_question = {}
        for m in poly_markets:
            key = self._normalize_question(m.question)
            poly_by_question[key] = m
        
        for km in kalshi_markets:
            key = self._normalize_question(km.question)
            pm = poly_by_question.get(key)
            
            if not pm:
                for pkey, pmarket in poly_by_question.items():
                    if self._fast_question_match(km.question, pmarket.question):
                        pm = pmarket
                        break
            
            if not pm:
                continue
            
            question_key = self._normalize_question(pm.question)
            if question_key in seen_questions:
                continue
            
            best_arb = self._find_best_arb(pm, km)
            if best_arb:
                candidates.append((best_arb, pm.question, km.question))
                seen_questions.add(question_key)
        
        candidates.sort(key=lambda x: x[0].spread or 0, reverse=True)
        
        signals = []
        for signal, q1, q2 in candidates:
            if signal.spread and signal.spread > 10:
                if self._ai_verify_same_question(q1, q2):
                    signals.append(signal)
                else:
                    logging.info(f"[Signals] AI rejected arb: {q1[:40]}... vs {q2[:40]}... (spread={signal.spread:.1f}%)")
            else:
                signals.append(signal)
        
        return signals
    
    def _fast_question_match(self, q1: str, q2: str) -> bool:
        n1 = self._normalize_question(q1)
        n2 = self._normalize_question(q2)
        return SequenceMatcher(None, n1, n2).ratio() > 0.6
    
    def _ai_verify_same_question(self, q1: str, q2: str) -> bool:
        matcher = self._get_matcher()
        result = matcher.are_same_question(q1, q2)
        logging.info(f"[Signals] AI verify: '{q1[:30]}...' vs '{q2[:30]}...' = {result.is_same} ({result.confidence:.0%}) - {result.reason}")
        return result.is_same and result.confidence >= 0.6
    
    def _find_best_arb(self, pm, km) -> Optional[Signal]:
        best_spread = 0
        best_outcome = None
        best_p_price = None
        best_k_price = None
        
        p_outcomes = {}
        if pm.is_multi_option and pm.options:
            for opt in pm.options:
                p_outcomes[self._normalize_name(opt.outcome)] = (opt.outcome, opt.price)
        else:
            p_outcomes[self._normalize_name(pm.lead_outcome or "yes")] = (pm.lead_outcome, pm.last_price)
        
        k_outcomes = {}
        if km.is_multi_option and km.options:
            for opt in km.options:
                k_outcomes[self._normalize_name(opt.outcome)] = (opt.outcome, opt.price)
        else:
            k_outcomes[self._normalize_name(km.lead_outcome or "yes")] = (km.lead_outcome, km.last_price)
        
        for p_key, (p_name, p_price) in p_outcomes.items():
            k_match = self._find_matching_outcome(p_key, p_name, k_outcomes)
            if k_match:
                k_name, k_price = k_match
                spread = abs(p_price - k_price)
                if spread > best_spread:
                    best_spread = spread
                    best_outcome = p_name
                    best_p_price = p_price
                    best_k_price = k_price
        
        if best_spread < self.MIN_ARB_SPREAD:
            return None
        
        spread_trace = MathTrace(
            formula_name="Arbitrage Spread",
            formula_latex="spread = |p_poly - p_kalshi|",
            inputs={"p_poly": best_p_price, "p_kalshi": best_k_price},
            calculation=f"|{best_p_price:.2f} - {best_k_price:.2f}| = {best_spread:.3f}",
            result=best_spread
        )
        
        if best_p_price < best_k_price:
            buy_platform, sell_platform = "polymarket", "kalshi"
            buy_price, sell_price = best_p_price, best_k_price
        else:
            buy_platform, sell_platform = "kalshi", "polymarket"
            buy_price, sell_price = best_k_price, best_p_price
        
        mid_price = (best_p_price + best_k_price) / 2
        model_p = mid_price + best_spread / 4
        
        ev, ev_trace = self.calculate_ev(model_p, buy_price)
        kelly, kelly_trace = self.calculate_kelly(model_p, 1/buy_price)
        
        math_traces = [t for t in [spread_trace, ev_trace, kelly_trace] if t]
        
        outcome_label = f" [{best_outcome}]" if best_outcome and best_outcome.lower() not in ['yes', 'no'] else ""
        
        return Signal(
            id=self._next_id(),
            signal_type=SignalType.ARBITRAGE,
            title=f"ARB: {pm.question[:50]}{outcome_label}",
            description=f"BUY {buy_platform.upper()} @ {buy_price*100:.0f}% → SELL {sell_platform.upper()} @ {sell_price*100:.0f}%",
            ev_percent=ev * 100,
            kelly_fraction=kelly,
            confidence=min(0.9, 0.5 + best_spread),
            poly_url=pm.url,
            kalshi_url=km.url,
            poly_price=best_p_price,
            kalshi_price=best_k_price,
            model_price=model_p,
            spread=best_spread * 100,
            category=pm.category or km.category or "GENERAL",
            math_traces=math_traces,
            volume=pm.volume + km.volume,
            liquidity=pm.liquidity + km.liquidity
        )
    
    def _normalize_name(self, name: str) -> str:
        if not name:
            return ""
        name = name.lower().strip()
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name)
        return name
    
    def _find_matching_outcome(self, p_key: str, p_name: str, k_outcomes: Dict) -> Optional[tuple]:
        if p_key in k_outcomes:
            return k_outcomes[p_key]
        
        best_match = None
        best_score = 0
        
        for k_key, (k_name, k_price) in k_outcomes.items():
            score = SequenceMatcher(None, p_key, k_key).ratio()
            
            if score > best_score and score >= 0.6:
                best_score = score
                best_match = (k_name, k_price)
        
        return best_match
    
    def detect_mispricings(self, markets: List, model_probs: Dict[str, float] = None) -> List[Signal]:
        signals = []
        
        for m in markets:
            model_p = None
            model_source = None
            
            if model_probs and m.id in model_probs:
                model_p = model_probs[m.id]
                model_source = "external_model"
            elif m.last_price < 0.15 or m.last_price > 0.85:
                continue
            
            if model_p is None:
                if m.one_hour_change and abs(m.one_hour_change) > 0.15:
                    if m.one_hour_change > 0:
                        model_p = min(0.95, m.last_price + m.one_hour_change * 0.3)
                    else:
                        model_p = max(0.05, m.last_price + m.one_hour_change * 0.3)
                    model_source = "momentum_extrapolation"
                else:
                    continue
            
            ev, ev_trace = self.calculate_ev(model_p, m.last_price)
            
            if abs(ev) >= self.MIN_EV_THRESHOLD:
                odds = 1 / m.last_price if m.last_price > 0 else 1
                kelly, kelly_trace = self.calculate_kelly(model_p, odds)
                
                model_trace = MathTrace(
                    formula_name="Model Probability",
                    formula_latex="p_model from " + model_source,
                    inputs={"market_price": m.last_price, "1h_change": m.one_hour_change or 0},
                    calculation=f"Source: {model_source} → p_model = {model_p:.2f}",
                    result=model_p
                )
                
                math_traces = [t for t in [model_trace, ev_trace, kelly_trace] if t]
                direction = "BUY" if ev > 0 else "SELL"
                
                signals.append(Signal(
                    id=self._next_id(),
                    signal_type=SignalType.MISPRICING,
                    title=f"{direction}: {m.question[:60]}",
                    description=f"Market: {m.last_price*100:.0f}% | Model: {model_p*100:.0f}% | Edge: {abs(ev)*100:.1f}%",
                    ev_percent=ev * 100,
                    kelly_fraction=kelly,
                    confidence=min(0.8, 0.4 + abs(ev)),
                    poly_url=m.url if m.platform == "polymarket" else None,
                    kalshi_url=m.url if m.platform == "kalshi" else None,
                    poly_price=m.last_price if m.platform == "polymarket" else None,
                    kalshi_price=m.last_price if m.platform == "kalshi" else None,
                    model_price=model_p,
                    category=m.category or "GENERAL",
                    math_traces=math_traces,
                    volume=m.volume,
                    liquidity=m.liquidity
                ))
        
        return sorted(signals, key=lambda s: abs(s.ev_percent), reverse=True)
    
    def extract_from_analysis(self, analysis_text: str) -> List[Signal]:
        signals = []
        if not analysis_text:
            return signals
        
        arb_pattern = r'\[([A-Z]+)\]\s*\[([^\]]+?)\s*\((\d+)%\)\]\(([^)]+)\)\s*vs\.?\s*\[([^\]]+?)\s*\((\d+)%\)\]\(([^)]+)\)\s*=\s*(\d+(?:\.\d+)?)\s*%'
        
        for match in re.finditer(arb_pattern, analysis_text, re.IGNORECASE):
            category, name1, price1, url1, name2, price2, url2, spread = match.groups()
            
            p1, p2 = float(price1) / 100, float(price2) / 100
            spread_val = float(spread) / 100 if float(spread) > 1 else float(spread)
            
            poly_url = url1 if 'polymarket' in url1.lower() else url2 if 'polymarket' in url2.lower() else None
            kalshi_url = url1 if 'kalshi' in url1.lower() else url2 if 'kalshi' in url2.lower() else None
            
            ev = spread_val / min(p1, p2) if min(p1, p2) > 0 else 0
            kelly, _ = self.calculate_kelly((p1 + p2) / 2 + spread_val / 4, 1 / min(p1, p2))
            
            signals.append(Signal(
                id=self._next_id(),
                signal_type=SignalType.ARBITRAGE,
                title=f"ARB: {name1[:50]}",
                description=f"{price1}% vs {price2}% = {spread}% spread",
                ev_percent=ev * 100,
                kelly_fraction=kelly,
                confidence=0.85,
                poly_url=poly_url,
                kalshi_url=kalshi_url,
                poly_price=p1 if poly_url == url1 else p2,
                kalshi_price=p1 if kalshi_url == url1 else p2,
                spread=spread_val * 100,
                category=category
            ))
        
        contrarian_pattern = r'\[([A-Z]+)\]\s*\[([^\]]+?)\s*\((\d+)%\)\]\(([^)]+)\)\s*[—–-]\s*(.+?)(?:CITE\[|$)'
        
        sections = analysis_text.split('## ')
        for section in sections:
            if 'CONTRARIAN' in section.upper():
                for match in re.finditer(contrarian_pattern, section, re.IGNORECASE | re.DOTALL):
                    category, title, price, url, reason = match.groups()
                    price_val = float(price) / 100
                    
                    model_p = 1 - price_val if price_val < 0.5 else price_val * 0.7
                    ev, _ = self.calculate_ev(model_p, price_val)
                    kelly, _ = self.calculate_kelly(model_p, 1/price_val)
                    
                    is_poly = 'polymarket' in url.lower()
                    
                    signals.append(Signal(
                        id=self._next_id(),
                        signal_type=SignalType.CONTRARIAN,
                        title=f"CONTRARIAN: {title[:50]}",
                        description=reason[:100].strip().replace('\n', ' '),
                        ev_percent=ev * 100,
                        kelly_fraction=kelly,
                        confidence=0.6,
                        poly_url=url if is_poly else None,
                        kalshi_url=url if not is_poly else None,
                        poly_price=price_val if is_poly else None,
                        kalshi_price=price_val if not is_poly else None,
                        model_price=model_p,
                        category=category
                    ))
        
        whale_pattern = r'\[([A-Z]+)\]\s*\[([^\]]+?)\s*\((\d+)%\)\]\(([^)]+)\)\s*[—–-]\s*\*\*(\w+)\*\*\s*\[([^\]]+)\][^\$]*\*\*\$?([\d,]+)\*\*'
        
        for section in sections:
            if 'WHALE' in section.upper():
                for match in re.finditer(whale_pattern, section, re.IGNORECASE):
                    category, title, price, url, action, outcome, amount = match.groups()
                    price_val = float(price) / 100
                    amount_val = float(amount.replace(',', ''))
                    
                    is_buy = action.upper() in ['BOUGHT', 'BUY']
                    model_p = min(0.95, price_val + 0.1) if is_buy else max(0.05, price_val - 0.1)
                    ev, _ = self.calculate_ev(model_p, price_val)
                    kelly, _ = self.calculate_kelly(model_p, 1/price_val)
                    
                    confidence = min(0.8, 0.5 + (amount_val / 1000000) * 0.3)
                    is_poly = 'polymarket' in url.lower()
                    
                    signals.append(Signal(
                        id=self._next_id(),
                        signal_type=SignalType.WHALE_FOLLOW,
                        title=f"WHALE: {action} {outcome} on {title[:40]}",
                        description=f"${amount} position @ {price}%",
                        ev_percent=ev * 100,
                        kelly_fraction=kelly,
                        confidence=confidence,
                        poly_url=url if is_poly else None,
                        kalshi_url=url if not is_poly else None,
                        poly_price=price_val if is_poly else None,
                        kalshi_price=price_val if not is_poly else None,
                        model_price=model_p,
                        category=category
                    ))
        
        key_market_pattern = r'\[([A-Z]+)\]\s*\[([^\]]+?)\s*\((\d+)%\)\]\(([^)]+)\)\s*[—–-]\s*(.+?)(?:CITE\[|$)'
        
        for section in sections:
            if 'KEY MARKET' in section.upper() and 'ARBITRAGE' not in section.upper():
                for match in re.finditer(key_market_pattern, section, re.IGNORECASE | re.DOTALL):
                    category, title, price, url, reason = match.groups()
                    price_val = float(price) / 100
                    
                    is_poly = 'polymarket' in url.lower()
                    
                    signals.append(Signal(
                        id=self._next_id(),
                        signal_type=SignalType.MOMENTUM,
                        title=f"KEY: {title[:50]}",
                        description=reason[:100].strip().replace('\n', ' '),
                        ev_percent=5.0,
                        kelly_fraction=0.05,
                        confidence=0.5,
                        poly_url=url if is_poly else None,
                        kalshi_url=url if not is_poly else None,
                        poly_price=price_val if is_poly else None,
                        kalshi_price=price_val if not is_poly else None,
                        category=category
                    ))
        
        return signals
    
    def _normalize_question(self, q: str) -> str:
        q = q.lower()
        q = re.sub(r'[^\w\s]', '', q)
        q = re.sub(r'\s+', ' ', q).strip()
        stopwords = {'will', 'the', 'a', 'an', 'be', 'to', 'in', 'on', 'at', 'by', 'for'}
        words = [w for w in q.split() if w not in stopwords]
        return ' '.join(words[:8])
    
    def _questions_match(self, q1: str, q2: str) -> bool:
        matcher = self._get_matcher()
        result = matcher.are_same_question(q1, q2)
        
        if not result.is_same:
            logging.debug(f"Questions don't match: '{q1[:40]}' vs '{q2[:40]}' - {result.reason}")
        
        return result.is_same and result.confidence >= 0.6
    
    def get_all_signals(self, poly_markets: List, kalshi_markets: List, 
                        analysis_text: str = None, model_probs: Dict = None) -> List[Signal]:
        all_signals = []
        
        logging.info(f"[Signals] Input: {len(poly_markets)} poly, {len(kalshi_markets)} kalshi, analysis={len(analysis_text or '')} chars")
        
        arb_signals = self.detect_arbitrage(poly_markets, kalshi_markets)
        logging.info(f"[Signals] Arbitrage: {len(arb_signals)} signals")
        all_signals.extend(arb_signals)
        
        all_markets = poly_markets + kalshi_markets
        mispricing_signals = self.detect_mispricings(all_markets, model_probs)
        logging.info(f"[Signals] Mispricing: {len(mispricing_signals)} signals")
        all_signals.extend(mispricing_signals[:10])
        
        if analysis_text:
            analysis_signals = self.extract_from_analysis(analysis_text)
            logging.info(f"[Signals] From AI analysis: {len(analysis_signals)} signals")
            existing_titles = {s.title.lower() for s in all_signals}
            for sig in analysis_signals:
                if sig.title.lower() not in existing_titles:
                    all_signals.append(sig)
        else:
            logging.info("[Signals] No AI analysis text available")
        
        all_signals.sort(key=lambda s: (
            -({'arbitrage': 4, 'whale_follow': 3, 'mispricing': 2, 'contrarian': 1}.get(s.signal_type.value, 0)),
            -abs(s.ev_percent),
            -s.confidence
        ))
        
        top_signals = all_signals[:20]
        for i, sig in enumerate(top_signals):
            sig.rank = i + 1
        
        logging.info(f"[Signals] Total: {len(top_signals)} signals returned")
        return top_signals
