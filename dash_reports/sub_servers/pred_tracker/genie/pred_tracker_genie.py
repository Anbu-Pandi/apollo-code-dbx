import logging
from typing import Dict, Any, List
from genai.sql_genie.clients.custom_genie import CustomGenieClient
from genai.argus_genies.tools.web_search.tool import search_google_news, search_ddg_html, _fetch_page_text


class PredTrackerGenie:

    def __init__(self):
        self.genie = CustomGenieClient(
            use_system_prompt=False,
            model_deployment_name="claude-opus-4-5",
            enable_notetaker=False,
            enable_learning=False
        )
        self.genie._skip_system_check = True
        self._news_citations = []

    def _search_news(self, query: str) -> tuple:
        logging.info(f"[News Tool] Searching: {query}")

        clean_query = query.strip()
        if len(clean_query) < 5:
            return "NO_NEWS_FOUND", None, None, None

        try:
            results = search_google_news(clean_query, max_results=5, freshness="week")
            if not results:
                logging.info(f"[News Tool] Google News empty, trying DuckDuckGo for: {clean_query[:40]}")
                results = search_ddg_html(clean_query, max_results=5)

            if not results:
                return "NO_NEWS_FOUND", None, None, None

            query_terms = set(clean_query.lower().split())
            for r in results:
                title_lower = (r.get("title") or "").lower()
                if any(term in title_lower for term in query_terms if len(term) > 3):
                    logging.info(f"[News Tool] Found: [{r.get('source')}] {r.get('title', '')[:50]} ({r.get('date')})")
                    return r.get("url", "NO_NEWS_FOUND"), r.get("source") or "News", (r.get("title") or "Article")[:60], r.get("date")

            r = results[0]
            return r.get("url", "NO_NEWS_FOUND"), r.get("source") or "News", (r.get("title") or "Article")[:60], r.get("date")
        except Exception as e:
            logging.warning(f"[News Tool] Search failed: {e}")
            return "NEWS_SEARCH_ERROR", None, None, None

    def _browse_link(self, url: str) -> str:
        logging.info(f"[Browse Tool] Fetching: {url[:80]}")
        if not url or len(url) < 10:
            return "[invalid url]"
        return _fetch_page_text(url, max_chars=6000)
    
    def _tool_executor(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "search_news":
            url, source_name, article_title, pub_date = self._search_news(tool_input.get("query", ""))

            if url not in ["NO_NEWS_FOUND", "NEWS_SEARCH_ERROR"]:
                citation_num = len(self._news_citations) + 1
                self._news_citations.append(url)
                source_tag = (source_name or "News").replace("|", "-")
                title_clean = (article_title or "Article").replace("|", "-").replace("[", "(").replace("]", ")").replace('"', "'").encode('ascii', 'ignore').decode('ascii').strip()
                if not title_clean:
                    title_clean = "Article"
                date_tag = f" ({pub_date})" if pub_date else ""
                return {"summary": f"Citation [{citation_num}] from {source_tag}{date_tag}", "result": f"CITE[{citation_num}|{url}|{source_tag}|{title_clean}|{pub_date or 'unknown'}]"}
            return {"summary": "No news found", "result": "Skip this citation - no relevant news within 14 days"}

        if tool_name == "browse_link":
            url = tool_input.get("url", "")
            content = self._browse_link(url)
            if content.startswith("["):
                return {"summary": f"Failed to fetch: {content}", "result": content}
            preview = content[:200].replace("\n", " ")
            return {"summary": f"Fetched {len(content)} chars from {url[:40]}", "result": content}

        return {"summary": "Unknown tool", "result": f"Tool '{tool_name}' not found"}
    
    def analyze_markets(self, poly_markets: List, kalshi_markets: List, recent_alerts: List, previous_analyses: List = None) -> Dict[str, Any]:
        from datetime import datetime
        self._news_citations = []
        
        now = datetime.now()
        context_header = f"""### ANALYSIS CONTEXT
**Current Time**: {now.strftime("%B %d, %Y %H:%M ET")}
**Markets**: {len(poly_markets)} Polymarket + {len(kalshi_markets)} Kalshi = {len(poly_markets) + len(kalshi_markets)} total
**Total Volume**: ${sum(m.volume for m in poly_markets + kalshi_markets)/1e6:.1f}M
**Recent Alerts**: {len(recent_alerts)} ({len([a for a in recent_alerts if a.event_type == 'WHALE_TRADE'])} whales, {len([a for a in recent_alerts if 'PRICE' in a.event_type])} price moves)
"""
        
        poly_summary = self._format_markets(poly_markets, "Polymarket")
        kalshi_summary = self._format_markets(kalshi_markets, "Kalshi")
        alerts_summary = self._format_alerts(recent_alerts)
        
        whale_alerts = [a for a in recent_alerts if a.event_type == "WHALE_TRADE"]
        has_whale_alerts = len(whale_alerts) > 0
        
        history_context = ""
        sections = []
        section_num = 1
        
        if previous_analyses:
            history_context = "\n### HISTORICAL AI SUMMARIES (Track Changes Over Time)\n"
            for i, prev in enumerate(reversed(previous_analyses[-5:]), 1):
                ts = prev.get('timestamp', '')[:16]
                analysis = prev.get('analysis', '')
                key_points = analysis[:500].replace('\n', ' ').strip()
                history_context += f"\n**{i}. [{ts}]**\n{key_points}...\n"
            
            sections.append(f"""## {section_num}. WHAT'S MOVING
- [CATEGORY] [Outcome (XX% → YY%)](url) — Trend over last runs: explain WHY with {{CITE_FROM_TOOL}}
- SKIP this section entirely if no meaningful changes (>5% moves) detected""")
            section_num += 1
        
        sections.append(f"""## {section_num}. KEY MARKETS
- [CATEGORY] [Exact Outcome (XX%)](market_url) — analysis. {{CITE_FROM_TOOL}}
- MUST include market_url from data above + news citation""")
        section_num += 1
        
        sections.append(f"""## {section_num}. ARBITRAGE OPPORTUNITIES  
- [CATEGORY] [Market on Poly (XX%)](poly_url) vs [Market on Kalshi (YY%)](kalshi_url) = spread%. {{CITE_FROM_TOOL}}
- MUST include BOTH platform URLs + news citation
- ONLY if spread >8%, otherwise write: *No arbitrage opportunities exceed 8% threshold.*""")
        section_num += 1
        
        if has_whale_alerts:
            whale_details = ', '.join([
                f'{a.market_question[:30]}... ({a.details.get("side","BUY")} {a.details.get("candidate","?")} {a.details.get("amount","?")} @ {a.details.get("price","?")})'
                for a in whale_alerts[:5]
            ])
            sections.append(f"""## {section_num}. WHALE ACTIVITY
- Analyze ONLY these whale trades: {whale_details}
- FORMAT EACH: [CATEGORY] [Market Name (XX%)](market_url) — **BOUGHT/SOLD** [Outcome] for **$amount** at **entry_price**. [Activity](activity_url) {{CITE_FROM_TOOL}}
- CRITICAL: Include ALL of: BUY/SELL direction + outcome name + $ amount + entry price
- Note: candidate already reflects effective direction (SELL Yes = bet on No)
- MUST include market_url + activity_url from data + news citation""")
            section_num += 1
        
        sections.append(f"""## {section_num}. CONTRARIAN BETS
- [CATEGORY] [Exact Outcome (XX%)](market_url) — why this is underpriced. {{CITE_FROM_TOOL}}
- MUST include market_url + news citation""")
        
        sections_text = "\n\n".join(sections)
        
        prompt = f"""Analyze prediction markets and identify interesting opportunities.

{context_header}

{poly_summary}

{kalshi_summary}

{alerts_summary}
{history_context}

**OUTPUT FORMAT** (EXACT):

# PREDICTION MARKET ANALYSIS

{sections_text}

**RULES**:
1. Every bullet starts with [CATEGORY] in CAPS (e.g., [CRYPTO], [POLITICS])
2. Every market MUST be a clickable link: [Market Name (XX%)](full_url_from_data)
3. **CRITICAL**: Copy the EXACT CITE string from search_news tool result - it contains CITE[N|url|source|title|date] format. Do NOT simplify to CITE[N|url].
4. Call search_news 10+ times minimum
5. For Arbitrage: include BOTH Polymarket AND Kalshi URLs side by side
6. Section numbers are dynamic - use exactly as shown above
7. NO uncited statements - every claim needs the full CITE string from tool
8. CATEGORY PRIORITY (in order): COMPANIES > FINANCIALS > EARNINGS > ECONOMICS > POLITICS > GEOPOLITICS > others. All crypto last.
9. CRYPTO LIMIT: Max 1 crypto mention per section. Treat these ALL as crypto: Bitcoin, Ethereum, Solana, XRP, Dogecoin, Memecoins, Altcoins, NFT, DeFi, any token/coin.
10. DIVERSITY: Each section should cover different categories - avoid repeating same category

**TEMPORAL CAUSATION WARNING** (CRITICAL):
- Today is {now.strftime("%m/%d/%Y")}. News citations include publication dates (MM/DD format).
- If a market moved on 01/27 but your citation is from 01/20 or 02/03, that news did NOT cause this move - it's either stale or post-hoc.
- ONLY cite news as explanation if publication date is within 24-48h BEFORE the price move timestamp.
- If no timely news exists, say "reason unclear" or "no recent catalyst found" - DO NOT fabricate causation.
- Humans are pattern-seeking creatures. Resist the urge to connect unrelated events just because they involve the same topic.
- Wrong: "Gold up 3% today [cites article from 8 days ago]" - this makes you look foolish.
- Right: "Gold up 3% today - no immediate catalyst identified" or cite same-day news if available.

Now analyze with 10+ CITE citations:"""

        tools = [{
            "name": "search_news",
            "description": "Search for recent news. Returns CITE[N|url|source|title|date] string with publication date (MM/DD). CHECK THE DATE - only use as explanation if within 24-48h of market move.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"]
            }
        }, {
            "name": "browse_link",
            "description": "Fetch and read full article text from a URL. Use to get details from a news article or market page when you need deeper context beyond the headline.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                "required": ["url"]
            }
        }]
        
        try:
            self.genie.system_prompt = prompt
            messages = [{"role": "user", "content": "Analyze the market data per the system prompt."}]
            
            response = self.genie._call_llm_with_tools(
                messages=messages,
                tools=tools,
                tool_executor=self._tool_executor,
                timer_label="Market Analysis",
                max_iterations=20
            )
            
            logging.info(f"[Analyst] Complete: {len(response['final_response'])} chars, {len(self._news_citations)} citations")
            
            return {
                "success": True,
                "analysis": response['final_response'],
                "market_count": {"polymarket": len(poly_markets), "kalshi": len(kalshi_markets)},
                "alert_count": len(recent_alerts)
            }
        except Exception as e:
            import traceback
            logging.error(f"[!] Analysis failed: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "analysis": f"Analysis unavailable: {str(e)}",
                "market_count": {"polymarket": 0, "kalshi": 0},
                "alert_count": 0
            }
    
    def _format_markets(self, markets: List, platform: str) -> str:
        if not markets:
            return f"No {platform} markets available"
        
        from collections import defaultdict
        by_category = defaultdict(list)
        for m in markets:
            cat = (m.category or "General").upper()
            by_category[cat].append(m)
        
        lines = [f"### {platform.upper()} MARKETS (Top 5 per Category)"]
        total_vol = sum(m.volume for m in markets)
        total_liq = sum(m.liquidity for m in markets)
        lines.append(f"Total: {len(markets)} markets | Vol: ${total_vol/1e6:.1f}M | Liq: ${total_liq/1e6:.1f}M\n")
        
        for cat in sorted(by_category.keys()):
            cat_markets = sorted(by_category[cat], key=lambda x: x.liquidity, reverse=True)[:5]
            lines.append(f"**[{cat}]** ({len(by_category[cat])} markets)")
            for m in cat_markets:
                change_str = ""
                if m.one_hour_change and abs(m.one_hour_change) >= 0.05:
                    change_str = f" | 1h: {m.one_hour_change*100:+.0f}%"
                elif m.one_day_change and abs(m.one_day_change) >= 0.10:
                    change_str = f" | 24h: {m.one_day_change*100:+.0f}%"
                lines.append(
                    f"  • {m.question[:80]}{'...' if len(m.question) > 80 else ''}\n"
                    f"    {m.lead_outcome}: {m.last_price*100:.0f}% | {m.second_outcome}: {(m.second_price or 0)*100:.0f}% | Vol: ${m.volume/1e3:.0f}K | Liq: ${m.liquidity/1e3:.0f}K{change_str}\n"
                    f"    {platform.upper()}_URL: {m.url}"
                )
            lines.append("")
        
        return "\n".join(lines)
    
    def _format_alerts(self, alerts: List) -> str:
        if not alerts:
            return "No recent alerts"
        
        from collections import defaultdict
        by_type = defaultdict(list)
        for a in alerts:
            by_type[a.event_type].append(a)
        
        lines = ["### RECENT ALERTS (Use these for analysis)"]
        
        type_labels = {
            "WHALE_TRADE": "WHALE TRADES (Large bets >$100K)",
            "PRICE_MOVEMENT_1H": "PRICE MOVES 1H (>20% swing)",
            "PRICE_MOVEMENT_24H": "PRICE MOVES 24H (>25% swing)"
        }
        
        for event_type, type_alerts in by_type.items():
            label = type_labels.get(event_type, event_type)
            sorted_alerts = sorted(type_alerts, key=lambda x: x.timestamp, reverse=True)[:20]
            lines.append(f"\n**{label}** ({len(type_alerts)} total)")
            
            for a in sorted_alerts:
                ts = a.timestamp.strftime("%m/%d %H:%M") if a.timestamp else "?"
                platform = a.platform.upper() if a.platform else "?"
                category = f"[{a.category.upper()}]" if a.category else ""
                change = a.details.get("change", "")
                amount = a.details.get("amount", "")
                lead = a.details.get("lead", "")
                vol = a.details.get("volume", "")
                liq = a.details.get("liquidity", "")
                
                if event_type == "WHALE_TRADE":
                    activity_url = a.details.get('activity_url', a.market_url)
                    trade_side = a.details.get('side', 'BUY')
                    trade_time = a.details.get('trade_time', ts)
                    entry_price = a.details.get('price', '?')
                    candidate = a.details.get('candidate', '?')
                    user = a.details.get('user', '?')
                    lines.append(
                        f"  [{ts}] {platform} {category} {a.market_question[:60]}...\n"
                        f"    {trade_side} {candidate} | {amount} @ {entry_price} | Trade: {trade_time} | User: {user}\n"
                        f"    Market: {lead} | Vol: {vol} | Liq: {liq}\n"
                        f"    URL: {a.market_url or 'N/A'}\n"
                        f"    ACTIVITY_URL: {activity_url}"
                    )
                else:
                    lines.append(
                        f"  [{ts}] {platform} {category} {a.market_question[:60]}...\n"
                        f"    Move: {change} → {lead} | Vol: {vol} | Liq: {liq}\n"
                        f"    URL: {a.market_url or 'N/A'}"
                    )
        
        return "\n".join(lines)
