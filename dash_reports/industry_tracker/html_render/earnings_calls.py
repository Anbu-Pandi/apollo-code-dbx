import json
import re
from typing import Dict, List

import pandas as pd

from dash_reports.industry_tracker.data.universe import Industry
from dash_reports.industry_tracker.html_render.formatters import esc

EARNINGS_CALLS_STYLE = """
<style>
.ern-module{margin-top:32px}
.ern-module .ern-section{margin:28px 0 0 0}
.ern-module .ern-section:first-child{margin-top:0}
.ern-module .ern-section-title{font-family:Garamond,'Times New Roman',serif;font-size:18px;font-weight:400;color:var(--apollo-green);letter-spacing:.3px;padding-bottom:6px;border-bottom:1px solid var(--apollo-green);margin-bottom:4px}
.ern-module .ern-section-note{font-family:'Segoe UI',sans-serif;font-weight:300;font-size:11px;color:var(--grey);margin:4px 0 14px 0;font-style:italic}
.ern-module .ern-theme-table-wrap{background:var(--white);border:1px solid var(--ecru);border-radius:4px;overflow:hidden}
.ern-module .ern-theme-table{width:100%;border-collapse:collapse;font-size:12px}
.ern-module .ern-theme-table th{background:var(--light-grey);color:var(--grey);padding:8px 12px;font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;text-align:left;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--ecru)}
.ern-module .ern-theme-table td{padding:10px 12px;border-bottom:1px solid #E5E0DB;vertical-align:middle;white-space:normal}
.ern-module .ern-theme-table tr:last-child td{border-bottom:none}
.ern-module .ern-theme-section-header td{background:#FAFAF9;font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--apollo-green);padding:6px 12px}
.ern-module .ern-theme-section-header.hw td{color:var(--matchstick)}
.ern-module .ern-theme-name{font-weight:600;color:var(--black);font-size:12px}
.ern-module .ern-bar-track{position:relative;background:var(--light-grey);height:18px;border-radius:2px;overflow:hidden;width:100%}
.ern-module .ern-bar-fill{height:100%;border-radius:2px;position:absolute;left:0;top:0}
.ern-module .ern-bar-fill.hw{background:var(--matchstick);opacity:.85}
.ern-module .ern-bar-fill.dr{background:var(--apollo-green);opacity:.85}
.ern-module .ern-bar-count{position:absolute;right:8px;top:50%;transform:translateY(-50%);font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;color:var(--black);z-index:1}
.ern-module .ern-takeaway{font-family:'Segoe UI',sans-serif;font-size:11px;line-height:1.5;color:var(--black)}
.ern-module .ern-seg-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.ern-module .ern-seg-card{background:var(--white);border:1px solid var(--ecru);border-radius:4px;padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.ern-module .ern-seg-header{display:flex;align-items:baseline;justify-content:space-between}
.ern-module .ern-seg-title{font-family:'Segoe UI',sans-serif;font-size:12.5px;font-weight:700;color:var(--black)}
.ern-module .ern-seg-count{font-family:'Segoe UI',sans-serif;font-size:10.5px;color:var(--grey);font-weight:300}
.ern-module .ern-seg-bar{display:flex;height:8px;border-radius:2px;overflow:hidden;background:var(--light-grey)}
.ern-module .ern-seg-bar-fill{height:100%}
.ern-module .ern-seg-bar-fill.r{background:var(--apollo-green)}
.ern-module .ern-seg-bar-fill.m{background:var(--grey);opacity:.4}
.ern-module .ern-seg-bar-fill.s{background:var(--clay)}
.ern-module .ern-seg-bar-fill.l{background:var(--matchstick)}
.ern-module .ern-seg-chips{display:flex;flex-wrap:wrap;gap:4px}
.ern-module .ern-seg-chip{font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:3px;border:1px solid transparent;letter-spacing:.3px}
.ern-module .ern-seg-chip.raised{background:rgba(0,123,99,.12);color:var(--apollo-green);border-color:rgba(0,123,99,.25)}
.ern-module .ern-seg-chip.maintained{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-module .ern-seg-chip.lowered{background:rgba(202,80,75,.12);color:var(--matchstick);border-color:rgba(202,80,75,.25)}
.ern-module .ern-seg-chip.suspended{background:rgba(147,134,122,.15);color:var(--clay);border-color:rgba(147,134,122,.30)}
.ern-module .ern-seg-chip.neutral{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-module .ern-seg-commentary{font-family:'Segoe UI',sans-serif;font-size:11px;line-height:1.5;color:var(--black)}
.ern-module .ern-seg-legend{display:flex;gap:16px;margin:6px 0 14px 0;font-family:'Segoe UI',sans-serif;font-size:10.5px;color:var(--grey);flex-wrap:wrap}
.ern-module .ern-seg-legend-item{display:flex;align-items:center;gap:5px}
.ern-module .ern-seg-legend-dot{display:inline-block;width:10px;height:10px;border-radius:2px}
.ern-module .ern-seg-legend-dot.r{background:var(--apollo-green)}
.ern-module .ern-seg-legend-dot.m{background:var(--grey);opacity:.4}
.ern-module .ern-seg-legend-dot.s{background:var(--clay)}
.ern-module .ern-seg-legend-dot.l{background:var(--matchstick)}
.ern-module .ern-issuer-table-wrap{background:var(--white);border:1px solid var(--ecru);border-radius:4px;overflow:hidden}
.ern-module .ern-issuer-table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px}
.ern-module .ern-issuer-table thead th{background:var(--light-grey);color:var(--grey);padding:8px 10px;font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;text-align:left;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--ecru)}
.ern-module .ern-issuer-row td{padding:10px;border-bottom:1px solid #E5E0DB;vertical-align:top;cursor:pointer;white-space:normal}
.ern-module .ern-issuer-row:hover td{background:#F5F9F8}
.ern-module .ern-issuer-row.expanded td{background:#FAFAF9}
.ern-module .ern-tk-col{width:18%}
.ern-module .ern-tk-pill{display:inline-block;background:var(--apollo-green);color:var(--white);font-family:'Segoe UI',sans-serif;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:3px;margin-right:6px;letter-spacing:.3px}
.ern-module .ern-co-mini{font-family:'Segoe UI',sans-serif;font-size:11.5px;font-weight:600;color:var(--black)}
.ern-module .ern-date-col{width:6%;color:var(--grey);font-size:11px;padding-top:13px!important}
.ern-module .ern-guide-col{width:9%;padding-top:11px!important}
.ern-module .ern-pos-col,.ern-module .ern-neg-col{font-size:11px;line-height:1.5}
.ern-module .ern-pos-col{border-left:2px solid rgba(0,123,99,.20)}
.ern-module .ern-neg-col{border-left:2px solid rgba(202,80,75,.20)}
.ern-module .ern-bullets{list-style:none;padding:0;margin:0}
.ern-module .ern-bullets li{position:relative;padding-left:12px;margin-bottom:4px;color:var(--black);white-space:normal;word-wrap:break-word;overflow-wrap:break-word}
.ern-module .ern-bullets li::before{content:"\\2022";position:absolute;left:0;top:0}
.ern-module .ern-bullets.pos li::before{color:var(--apollo-green)}
.ern-module .ern-bullets.neg li::before{color:var(--matchstick)}
.ern-module .ern-expand-col{width:32px;text-align:center;padding-top:11px!important}
.ern-module .ern-expand-icon{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;background:#FAFAF9;color:var(--apollo-green);font-size:14px;font-weight:600;line-height:1}
.ern-module .ern-issuer-row.expanded .ern-expand-icon{background:var(--apollo-green);color:var(--white)}
.ern-module .ern-guide-pill{display:inline-block;padding:3px 8px;border-radius:3px;font-family:'Segoe UI',sans-serif;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;border:1px solid transparent}
.ern-module .ern-guide-pill.raised{background:rgba(0,123,99,.10);color:var(--apollo-green);border-color:rgba(0,123,99,.25)}
.ern-module .ern-guide-pill.maintained{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-module .ern-guide-pill.lowered{background:rgba(202,80,75,.10);color:var(--matchstick);border-color:rgba(202,80,75,.25)}
.ern-module .ern-guide-pill.suspended{background:rgba(147,134,122,.12);color:var(--clay);border-color:rgba(147,134,122,.30)}
.ern-module .ern-guide-pill.neutral{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-module .ern-eps-col{width:10%;padding-top:9px!important}
.ern-module .ern-sales-col{width:11%;padding-top:9px!important}
.ern-module .ern-eps-cell{font-family:'Segoe UI',sans-serif;font-size:11px;line-height:1.4;display:flex;flex-direction:column;gap:2px}
.ern-module .ern-eps-vals{color:var(--black);white-space:nowrap;font-variant-numeric:tabular-nums}
.ern-module .ern-eps-vals .est{color:var(--grey)}
.ern-module .ern-eps-surprise{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;letter-spacing:.3px;border:1px solid transparent;font-variant-numeric:tabular-nums;align-self:flex-start}
.ern-module .ern-eps-surprise.beat{background:rgba(0,123,99,.10);color:var(--apollo-green);border-color:rgba(0,123,99,.25)}
.ern-module .ern-eps-surprise.miss{background:rgba(202,80,75,.10);color:var(--matchstick);border-color:rgba(202,80,75,.25)}
.ern-module .ern-eps-surprise.flat{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-module .ern-eps-empty{color:var(--grey);font-style:italic;font-size:11px}
.ern-module .ern-issuer-detail td{padding:0!important;background:#FAFAF9!important;border-bottom:1px solid var(--ecru)!important}
.ern-module .ern-issuer-detail[hidden]{display:none}
.ern-module .ern-detail-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;padding:16px 18px 18px 18px}
.ern-module .ern-detail-block{min-width:0}
.ern-module .ern-detail-block.full{grid-column:1/-1;min-width:0}
.ern-module .ern-detail-eyebrow{font-family:'Segoe UI',sans-serif;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--grey);margin-bottom:4px}
.ern-module .ern-detail-eyebrow.pos{color:var(--apollo-green)}
.ern-module .ern-detail-eyebrow.neg{color:var(--matchstick)}
.ern-module .ern-detail-body{font-family:'Segoe UI',sans-serif;font-size:11px;line-height:1.5;color:var(--black);white-space:normal;word-wrap:break-word;overflow-wrap:break-word}
.ern-module .ern-empty{padding:32px;text-align:center;color:var(--grey);font-size:12px;background:var(--white);border:1px solid var(--ecru);border-radius:4px}
.ern-module .ern-source{margin-top:18px;padding:10px 14px;background:#FAFAF9;border-left:3px solid var(--ecru);font-size:10.5px;color:var(--grey);font-style:italic;line-height:1.5}
.ern-module .ern-hover-trigger{cursor:help;transition:background 120ms ease}
.ern-module td.ern-takeaway.ern-hover-trigger:hover,.ern-module .ern-seg-commentary.ern-hover-trigger:hover{background:rgba(0,123,99,.05)}
.ern-module td.ern-pos-col.ern-hover-trigger:hover{background:rgba(0,123,99,.06)}
.ern-module td.ern-neg-col.ern-hover-trigger:hover{background:rgba(202,80,75,.06)}
.ern-module .ern-detail-block.ern-hover-trigger{padding:4px 6px;margin:-4px -6px;border-radius:3px;transition:background 120ms ease}
.ern-module .ern-detail-block.ern-hover-trigger:hover{background:rgba(0,123,99,.05)}
.ern-pop{position:absolute;z-index:9999;width:480px;max-height:min(540px,80vh);overflow-y:auto;background:var(--white);border:1px solid var(--ecru);border-left:3px solid var(--apollo-green);box-shadow:0 10px 28px rgba(0,0,0,0.18);padding:12px 14px;font-family:'Segoe UI',sans-serif;font-size:11px;color:var(--black);line-height:1.5;display:none}
.ern-pop.visible{display:block}
.ern-pop-title{font-family:Garamond,'Times New Roman',serif;font-size:13px;font-weight:400;color:var(--apollo-green);letter-spacing:.3px;padding-bottom:6px;margin-bottom:8px;border-bottom:1px solid var(--apollo-green);position:sticky;top:-12px;background:var(--white);padding-top:2px}
.ern-pop-title .count{font-family:'Segoe UI',sans-serif;font-size:10px;color:var(--grey);font-weight:300;font-style:italic;margin-left:8px;letter-spacing:0}
.ern-pop-empty{color:var(--grey);font-style:italic;padding:8px 0}
.ern-pop-row{padding:8px 0;border-bottom:1px solid #E5E0DB}
.ern-pop-row:last-of-type{border-bottom:none}
.ern-pop-row-head{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:10px;flex-wrap:wrap}
.ern-pop-tk{display:inline-block;background:var(--apollo-green);color:var(--white);font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.3px}
.ern-pop-bucket{font-size:9px;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:2px;letter-spacing:.3px;border:1px solid transparent}
.ern-pop-bucket.positive{background:rgba(0,123,99,.10);color:var(--apollo-green);border-color:rgba(0,123,99,.25)}
.ern-pop-bucket.negative{background:rgba(202,80,75,.10);color:var(--matchstick);border-color:rgba(202,80,75,.25)}
.ern-pop-bucket.guidance{background:rgba(147,134,122,.12);color:var(--clay);border-color:rgba(147,134,122,.30)}
.ern-pop-bucket.neutral{background:#FAFAF9;color:var(--grey);border-color:var(--ecru)}
.ern-pop-bucket.mixed{background:rgba(147,134,122,.12);color:var(--clay);border-color:rgba(147,134,122,.30)}
.ern-pop-date{color:var(--grey)}
.ern-pop-sim{margin-left:auto;color:var(--grey);font-variant-numeric:tabular-nums}
.ern-pop-speaker{font-size:10px;color:var(--grey);font-style:italic;margin-bottom:2px}
.ern-pop-content{font-size:11px;color:var(--black);line-height:1.5}
.ern-pop-content mark.ern-mark{padding:0 2px;border-radius:2px;font-weight:700;background:rgba(0,123,99,.18);color:var(--apollo-green)}
.ern-pop-content mark.ern-mark.positive{background:rgba(0,123,99,.18);color:var(--apollo-green)}
.ern-pop-content mark.ern-mark.negative{background:rgba(202,80,75,.18);color:var(--matchstick)}
.ern-pop-content mark.ern-mark.guidance{background:rgba(147,134,122,.18);color:var(--clay)}
.ern-pop-content mark.ern-mark.neutral{background:#F1EEEA;color:var(--grey)}
.ern-pop-content mark.ern-mark.mixed{background:rgba(147,134,122,.18);color:var(--clay)}
.ern-pop-nav{display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding:8px 0 2px;border-top:1px solid var(--ecru);font-size:10px;position:sticky;bottom:-12px;background:var(--white)}
.ern-pop-nav button{background:var(--white);border:1px solid var(--ecru);color:var(--apollo-green);padding:3px 12px;cursor:pointer;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;border-radius:3px;font-family:'Segoe UI',sans-serif}
.ern-pop-nav button:hover:not(:disabled){background:rgba(0,123,99,.08);border-color:var(--apollo-green)}
.ern-pop-nav button:disabled{color:var(--grey);cursor:not-allowed;opacity:.45}
.ern-pop-nav .page-info{color:var(--grey);font-variant-numeric:tabular-nums;font-style:italic}
@media(max-width:1100px){.ern-module .ern-seg-grid{grid-template-columns:1fr 1fr}.ern-module .ern-detail-grid{grid-template-columns:1fr}}
</style>
"""

EARNINGS_CALLS_SCRIPT = """
<script>
(function() {
    if (window.__ernHoverInit) return;
    window.__ernHoverInit = true;
    function init() {
        var data = window.__ERN_EVIDENCE__ || {pool: [], keys: {}};
        var pool = data.pool || [];
        var keys = data.keys || {};
        var PER_PAGE = 5;
        var pop = document.createElement('div');
        pop.className = 'ern-pop';
        document.body.appendChild(pop);
        var hideTimer = null;
        var currentEntry = null;
        var currentPage = 0;
        var currentTarget = null;
        function clearHide() { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; } }
        function scheduleHide() { clearHide(); hideTimer = setTimeout(function() { pop.classList.remove('visible'); }, 220); }
        pop.addEventListener('mouseenter', clearHide);
        pop.addEventListener('mouseleave', scheduleHide);
        function escHtml(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }
        var REGEX_SPECIALS = '.*+?^${}()|[]/';
        function escRegex(s) {
            var out = '';
            var src = String(s == null ? '' : s);
            for (var i = 0; i < src.length; i++) {
                var ch = src.charAt(i);
                if (ch === '\\\\' || REGEX_SPECIALS.indexOf(ch) >= 0) {
                    out += '\\\\' + ch;
                } else {
                    out += ch;
                }
            }
            return out;
        }
        function rowRegexes(highlights) {
            return (highlights || []).map(function(h) {
                var trimmed = String(h || '').trim();
                if (!trimmed) return null;
                try { return new RegExp(escRegex(trimmed), 'gi'); } catch (e) { return null; }
            }).filter(Boolean);
        }
        function highlight(text, regexes, sentClass) {
            var safe = escHtml(text);
            var open = '<mark class="ern-mark ' + (sentClass || '') + '">';
            regexes.forEach(function(re) {
                safe = safe.replace(re, function(m) { return open + m + '</mark>'; });
            });
            return safe;
        }
        function renderPage(entry, page) {
            var rows = (entry.rows || []).map(function(i) { return pool[i]; }).filter(Boolean);
            var totalPages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
            page = Math.max(0, Math.min(totalPages - 1, page));
            var slice = rows.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
            var html = '<div class="ern-pop-title">' + escHtml(entry.title || 'Raw evidence') +
                       '<span class="count">' + rows.length + ' snippets &middot; sorted by similarity</span></div>';
            if (!slice.length) {
                html += '<div class="ern-pop-empty">No transcript snippets cached for this item.</div>';
            }
            slice.forEach(function(r) {
                var sim = r.s ? r.s.toFixed(2) : '';
                var label = (r.s2 || r.b || '').toLowerCase();
                var labelHtml = label ? '<span class="ern-pop-bucket ' + escHtml(label) + '">' + escHtml(label) + '</span>' : '';
                html += '<div class="ern-pop-row">' +
                    '<div class="ern-pop-row-head">' +
                        '<span class="ern-pop-tk">' + escHtml(r.t) + '</span>' +
                        labelHtml +
                        '<span class="ern-pop-date">' + escHtml(r.d) + '</span>' +
                        (sim ? '<span class="ern-pop-sim">sim ' + escHtml(sim) + '</span>' : '') +
                    '</div>' +
                    (r.k ? '<div class="ern-pop-speaker">' + escHtml(r.k) + '</div>' : '') +
                    '<div class="ern-pop-content">' + highlight(r.c || '', rowRegexes(r.h), label) + '</div>' +
                '</div>';
            });
            if (totalPages > 1) {
                html += '<div class="ern-pop-nav">' +
                    '<button data-pop-action="prev"' + (page <= 0 ? ' disabled' : '') + '>&#8592; Prev</button>' +
                    '<span class="page-info">Page ' + (page + 1) + ' / ' + totalPages + '</span>' +
                    '<button data-pop-action="next"' + (page >= totalPages - 1 ? ' disabled' : '') + '>Next &#8594;</button>' +
                '</div>';
            }
            return html;
        }
        function position(target) {
            var rect = target.getBoundingClientRect();
            var W = 480, M = 12;
            var left = rect.left;
            if (left + W > window.innerWidth - M) left = window.innerWidth - W - M;
            if (left < M) left = M;
            pop.style.left = (window.scrollX + left) + 'px';
            pop.style.top = (window.scrollY + rect.bottom + 6) + 'px';
            requestAnimationFrame(function() {
                var pr = pop.getBoundingClientRect();
                if (pr.bottom > window.innerHeight - M && rect.top - pr.height - 6 > M) {
                    pop.style.top = (window.scrollY + rect.top - pr.height - 6) + 'px';
                }
            });
        }
        function show(target) {
            var key = target.getAttribute('data-ev-key');
            if (!key) return;
            currentTarget = target;
            currentEntry = keys[key];
            currentPage = 0;
            clearHide();
            if (!currentEntry) {
                pop.innerHTML = '<div class="ern-pop-title">Raw evidence</div>' +
                                '<div class="ern-pop-empty">No raw evidence cached for this item.</div>';
            } else {
                pop.innerHTML = renderPage(currentEntry, currentPage);
            }
            pop.classList.add('visible');
            pop.scrollTop = 0;
            position(target);
        }
        pop.addEventListener('click', function(e) {
            var btn = e.target.closest && e.target.closest('button[data-pop-action]');
            if (!btn || !currentEntry) return;
            e.stopPropagation();
            var action = btn.getAttribute('data-pop-action');
            if (action === 'next') currentPage++;
            else if (action === 'prev') currentPage--;
            pop.innerHTML = renderPage(currentEntry, currentPage);
            pop.scrollTop = 0;
            if (currentTarget) position(currentTarget);
        });
        document.querySelectorAll('.ern-module .ern-hover-trigger').forEach(function(el) {
            el.addEventListener('mouseenter', function() { show(el); });
            el.addEventListener('mouseleave', scheduleHide);
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
function toggleErnIssuer(idx) {
    var row = document.querySelector('.ern-module .ern-issuer-row[data-idx="' + idx + '"]');
    var detail = document.getElementById('ern-detail-' + idx);
    if (!row || !detail) return;
    var willExpand = detail.hasAttribute('hidden');
    if (willExpand) {
        detail.removeAttribute('hidden');
        row.classList.add('expanded');
        var openIcon = row.querySelector('.ern-expand-icon');
        if (openIcon) openIcon.textContent = '-';
    } else {
        detail.setAttribute('hidden', '');
        row.classList.remove('expanded');
        var closeIcon = row.querySelector('.ern-expand-icon');
        if (closeIcon) closeIcon.textContent = '+';
    }
}
</script>
"""

THEMES = [
    ('Headwinds', 'hw', [
        ('Tariffs', r'\btariff|dut(y|ies)\b|import cost'),
        ('Middle East Conflict', r'Middle East|Red Sea|geopolitical|conflict|shipping disruption|rerout'),
        ('Commodity & Energy Costs', r'inflation|input cost|commodity|freight|labor cost|oil|energy|raw material'),
        ('Currency / FX', r'\bfx\b|foreign exchange|currency|dollar|euro|yen'),
        ('Consumer Weakness', r'consumer (weak|soft|pressure|cautious)|demand (soft|weak|pressure)|traffic decline|promotional'),
        ('Supply Chain', r'supply chain|logistics|shipping|inventory disruption|lead time'),
    ]),
    ('Drivers', 'dr', [
        ('Geographic Expansion', r'international|Asia|Europe|Latin America|APAC|China|geographic'),
        ('Product Innovation', r'innovation|new product|launch|pipeline|premium'),
        ('E-commerce', r'e-commerce|digital|online'),
        ('Premium / Luxury', r'premium|luxury|high end|high-end|TUMI|beauty'),
        ('DTC / Direct Channel', r'direct-to-consumer|DTC|direct channel|owned retail'),
        ('Market Share Gains', r'market share|share gain|share gains|outperform'),
    ]),
]

GUIDANCE_RULES = [
    ('suspended', r'suspend|suspended|withdraw|withdrew|no longer providing'),
    ('lowered', r'lowered|reduced|cut|downward|below prior|guide-down|trimmed'),
    ('raised', r'raised|increased|lifted|above prior|better than previously|upward'),
    ('maintained', r'maintained|reaffirmed|confirmed|unchanged|reiterated'),
]


def _trim(text: str, n: int = 220) -> str:
    text = (text or '').strip()
    text = re.sub(r'\s+', ' ', text)
    if len(text) <= n:
        return text
    cut = text[:n]
    last = max(cut.rfind('. '), cut.rfind('? '), cut.rfind('! '))
    if last >= n - 60:
        return cut[:last + 1].strip()
    return cut.rstrip(',;:- ') + '...'


def _split_group(sub_group: str) -> tuple:
    parts = str(sub_group or '').split(' / ', 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return '', str(sub_group or '')


def _bullets_html(items: List[dict], css_class: str, max_n: int = 4) -> str:
    items = items[:max_n]
    if not items:
        return '<span class="ern-detail-body">No salient quotes found.</span>'
    lis = ''.join(f'<li>{esc(_trim(it.get("content"), 170))}</li>' for it in items if it.get('content'))
    if not lis:
        return '<span class="ern-detail-body">No salient quotes found.</span>'
    return f'<ul class="ern-bullets {css_class}">{lis}</ul>'


def _detail_text(items: List[dict], fallback: str) -> str:
    if not items:
        return fallback
    parts = []
    for it in items:
        speaker = (it.get('speaker') or '').strip()
        prefix = f'{speaker}: ' if speaker else ''
        parts.append(prefix + _trim(it.get('content'), 420))
    return ' '.join(parts)


def _date_label(d) -> str:
    ts = pd.to_datetime(d, errors='coerce')
    if pd.isna(ts):
        return ''
    return ts.strftime('%b %d')


def _eps_index(eps_df) -> Dict[str, dict]:
    if eps_df is None or len(eps_df) == 0:
        return {}
    out = {}
    for r in eps_df.to_dict('records'):
        tkr = r.get('ticker_region') or ''
        if tkr:
            out[tkr] = r
    return out


def _eps_num(val) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ''
    if pd.isna(v):
        return ''
    return f'{v:,.2f}'


def _sales_num(val) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ''
    if pd.isna(v):
        return ''
    abs_v = abs(v)
    if abs_v >= 1000:
        return f'${v / 1000.0:,.2f}B'
    return f'${v:,.0f}mm'


def _metric_cell(actual: str, estimate: str, surprise) -> str:
    try:
        surprise_f = float(surprise) if surprise is not None and not pd.isna(surprise) else None
    except (TypeError, ValueError):
        surprise_f = None
    if not actual and surprise_f is None:
        return '<div class="ern-eps-empty">&mdash;</div>'
    vals = ''
    if actual:
        vals = f'<div class="ern-eps-vals"><span class="act">{esc(actual)}</span>'
        if estimate:
            vals += f' <span class="est">/ {esc(estimate)}</span>'
        vals += '</div>'
    pill = ''
    if surprise_f is not None:
        cls = 'beat' if surprise_f > 0.5 else ('miss' if surprise_f < -0.5 else 'flat')
        sign = '+' if surprise_f > 0 else ''
        pill = f'<span class="ern-eps-surprise {cls}">{sign}{surprise_f:.1f}%</span>'
    return f'<div class="ern-eps-cell">{vals}{pill}</div>'


def _eps_cell(rec_eps: dict) -> str:
    if not rec_eps:
        return '<div class="ern-eps-empty">&mdash;</div>'
    return _metric_cell(
        _eps_num(rec_eps.get('eps_actual')),
        _eps_num(rec_eps.get('eps_estimate_mean')),
        rec_eps.get('eps_surprise_pct'),
    )


def _sales_cell(rec_eps: dict) -> str:
    if not rec_eps:
        return '<div class="ern-eps-empty">&mdash;</div>'
    return _metric_cell(
        _sales_num(rec_eps.get('sales_actual')),
        _sales_num(rec_eps.get('sales_estimate_mean')),
        rec_eps.get('sales_surprise_pct'),
    )


def _guidance(items: List[dict]) -> str:
    text = ' '.join(str(it.get('content') or '') for it in items)
    for label, pattern in GUIDANCE_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return 'neutral'


def _guidance_items(items: List[dict]) -> List[dict]:
    out = []
    for it in items:
        if re.search(r'guidance|outlook|raised|lowered|maintained|reaffirmed|suspended|forecast', str(it.get('content') or ''), flags=re.IGNORECASE):
            out.append(it)
    return out[:2]


def _issuer_records(df_quotes: pd.DataFrame) -> List[Dict]:
    if df_quotes is None or df_quotes.empty:
        return []

    out = []
    issuers = (df_quotes
               .sort_values(['call_date', 'bucket', 'rank'], ascending=[False, True, True])
               .groupby('ticker_region', sort=False))
    for _, grp in issuers:
        first = grp.iloc[0]
        pos = grp[grp['bucket'] == 'positive'].sort_values('rank').to_dict('records')
        neg = grp[grp['bucket'] == 'negative'].sort_values('rank').to_dict('records')
        guide = grp[grp['bucket'] == 'guidance'].sort_values('rank').to_dict('records') \
            if 'guidance' in grp['bucket'].values else []
        items = pos + neg + guide
        _, sub_label = _split_group(first.get('sub_group') or '')
        guidance_label = _guidance(guide) if guide else _guidance(pos + neg)
        out.append({
            'sub_group': first.get('sub_group') or '',
            'sub_label': sub_label,
            'ticker': first.get('ticker') or str(first.get('ticker_region') or '').split('-')[0],
            'ticker_region': first.get('ticker_region') or '',
            'name': first.get('name') or '',
            'call_date': first.get('call_date'),
            'guidance': guidance_label,
            'pos': pos,
            'neg': neg,
            'guide': guide,
            'guidance_items': guide[:2] if guide else _guidance_items(items),
            'items': items,
        })
    return out


_VECTOR_VERSION_RE = re.compile(r'-[rt]_')


def _chunk_key(vector_id: str) -> str:
    return _VECTOR_VERSION_RE.sub('_', vector_id or '')


def _evidence_row(item: dict, chunk_meta: dict) -> dict:
    vid = item.get('vector_id') or ''
    meta = (chunk_meta.get(_chunk_key(vid)) if vid else None) or {}
    sentiment = (meta.get('sentiment') or '').strip().lower()
    highlights = [str(h).strip() for h in (meta.get('highlights') or []) if str(h or '').strip()]
    return {
        't': item.get('ticker') or '',
        'd': str(item.get('call_date') or ''),
        'b': item.get('bucket') or '',
        's2': sentiment,
        's': round(float(item.get('similarity') or 0.0), 3),
        'k': (item.get('speaker') or '').strip(),
        'c': _trim(item.get('content') or '', 600),
        'h': highlights[:3],
    }


def _build_evidence_map(records: List[Dict], chunk_meta: dict = None) -> dict:
    chunk_meta = chunk_meta or {}
    pool: list = []
    pool_idx: Dict[str, int] = {}

    def _add(it: dict, ticker: str) -> int:
        vid = it.get('vector_id') or f'{ticker}|{it.get("rank")}|{it.get("bucket")}|{it.get("content", "")[:60]}'
        if vid in pool_idx:
            return pool_idx[vid]
        row = _evidence_row({**it, 'ticker': ticker}, chunk_meta)
        pool_idx[vid] = len(pool)
        pool.append(row)
        return pool_idx[vid]

    keys: Dict[str, dict] = {}

    all_items = []
    for rec in records:
        for it in rec['items']:
            all_items.append((it, rec['ticker']))

    for _, _, themes in THEMES:
        for name, pattern in themes:
            hits = [(it, tk) for it, tk in all_items
                    if re.search(pattern, str(it.get('content') or ''), flags=re.IGNORECASE)]
            hits.sort(key=lambda x: float(x[0].get('similarity') or 0.0), reverse=True)
            keys[f'theme:{name}'] = {
                'title': f'{name} \u2014 cross-universe evidence',
                'rows': [_add(it, tk) for it, tk in hits[:30]],
            }

    by_seg: Dict[str, list] = {}
    for rec in records:
        for it in rec['items']:
            by_seg.setdefault(rec['sub_group'], []).append((it, rec['ticker']))
    for sub_group, items in by_seg.items():
        items_sorted = sorted(items, key=lambda x: float(x[0].get('similarity') or 0.0), reverse=True)
        _, label = _split_group(sub_group)
        keys[f'seg:{sub_group}'] = {
            'title': f'{label} \u2014 segment evidence',
            'rows': [_add(it, tk) for it, tk in items_sorted[:30]],
        }

    def _llm_sentiment(it: dict) -> str:
        vid = it.get('vector_id') or ''
        meta = (chunk_meta.get(_chunk_key(vid)) if vid else None) or {}
        return (meta.get('sentiment') or '').strip().lower()

    for rec in records:
        tkrgn = rec['ticker_region']
        ticker = rec['ticker']
        all_sorted = sorted(rec['items'], key=lambda x: float(x.get('similarity') or 0.0), reverse=True)
        pos_items = [it for it in all_sorted if _llm_sentiment(it) in ('positive', 'mixed')]
        neg_items = [it for it in all_sorted if _llm_sentiment(it) in ('negative', 'mixed')]
        guide_items = [it for it in all_sorted if _llm_sentiment(it) == 'guidance']
        keys[f'issuer:{tkrgn}:pos'] = {
            'title': f'{ticker} \u2014 positive transcript evidence',
            'rows': [_add(it, ticker) for it in pos_items],
        }
        keys[f'issuer:{tkrgn}:neg'] = {
            'title': f'{ticker} \u2014 negative transcript evidence',
            'rows': [_add(it, ticker) for it in neg_items],
        }
        keys[f'issuer:{tkrgn}:all'] = {
            'title': f'{ticker} \u2014 all transcript evidence',
            'rows': [_add(it, ticker) for it in all_sorted],
        }
        if guide_items:
            keys[f'issuer:{tkrgn}:guide'] = {
                'title': f'{ticker} \u2014 guidance evidence',
                'rows': [_add(it, ticker) for it in guide_items],
            }

    return {'pool': pool, 'keys': keys}


def _theme_counts(records: List[Dict], commentary: dict = None) -> str:
    theme_takeaways = (commentary or {}).get('theme_takeaways') or {}
    all_items = []
    for rec in records:
        for it in rec['items']:
            row = dict(it)
            row['ticker'] = rec['ticker']
            all_items.append(row)
    max_count = max(1, len(records))
    rows = []
    for section, css, themes in THEMES:
        header_cls = 'ern-theme-section-header hw' if css == 'hw' else 'ern-theme-section-header'
        rows.append(f'<tr class="{header_cls}"><td colspan="3">{esc(section)}</td></tr>')
        for name, pattern in themes:
            hits = [it for it in all_items if re.search(pattern, str(it.get('content') or ''), flags=re.IGNORECASE)]
            tickers = sorted({str(it.get('ticker') or '') for it in hits if it.get('ticker')})
            count = len(tickers)
            width = int(round(count / max_count * 100)) if count else 0
            snippets = []
            for it in sorted(hits, key=lambda x: float(x.get('similarity') or 0), reverse=True)[:2]:
                snippets.append(f"{it.get('ticker')}: {_trim(it.get('content'), 135)}")
            takeaway = theme_takeaways.get(name) or 'Recent reporters mention this theme, but no synthesized takeaway is cached yet.'
            rows.append(f"""<tr>
            <td><div class="ern-theme-name">{esc(name)}</div></td>
            <td><div class="ern-bar-track"><div class="ern-bar-fill {css}" style="width:{width}%;"></div><div class="ern-bar-count">{count}</div></div></td>
            <td class="ern-takeaway ern-hover-trigger" data-ev-key="theme:{esc(name)}">{esc(takeaway)}</td>
        </tr>""")
    return ''.join(rows)


def _segment_cards(records: List[Dict], commentary: dict = None) -> str:
    segment_commentary = (commentary or {}).get('segment_commentary') or {}
    grouped: Dict[str, List[Dict]] = {}
    for rec in records:
        grouped.setdefault(rec['sub_group'], []).append(rec)
    cards = []
    for sub_group, recs in sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0])):
        _, title = _split_group(sub_group)
        n = len(recs)
        counts = {k: len([r for r in recs if r['guidance'] == k]) for k in ['raised', 'maintained', 'suspended', 'lowered']}
        neutral = len([r for r in recs if r['guidance'] == 'neutral'])
        maintained = counts['maintained'] + neutral
        bar = ''.join([
            f'<div class="ern-seg-bar-fill r" style="width:{counts["raised"] / n * 100:.0f}%;"></div>' if counts['raised'] else '',
            f'<div class="ern-seg-bar-fill m" style="width:{maintained / n * 100:.0f}%;"></div>' if maintained else '',
            f'<div class="ern-seg-bar-fill s" style="width:{counts["suspended"] / n * 100:.0f}%;"></div>' if counts['suspended'] else '',
            f'<div class="ern-seg-bar-fill l" style="width:{counts["lowered"] / n * 100:.0f}%;"></div>' if counts['lowered'] else '',
        ])
        chips = ''.join(f'<span class="ern-seg-chip {esc(r["guidance"])}">{esc(r["ticker"])}</span>' for r in recs[:12])
        body = segment_commentary.get(sub_group) or segment_commentary.get(title) or f'{title} has mixed earnings evidence across {n} recent reporter{"s" if n != 1 else ""}; issuer rows carry the detailed driver/headwind read-through.'
        cards.append(f"""<div class="ern-seg-card">
            <div class="ern-seg-header"><div class="ern-seg-title">{esc(title)}</div><div class="ern-seg-count">{n} issuer{'s' if n != 1 else ''}</div></div>
            <div class="ern-seg-bar">{bar}</div>
            <div class="ern-seg-chips">{chips}</div>
            <div class="ern-seg-commentary ern-hover-trigger" data-ev-key="seg:{esc(sub_group)}">{esc(body or 'Transcript snippets were available, but no dominant theme stood out in the cached quotes.')}</div>
        </div>""")
    return ''.join(cards)


def _issuer_rows(records: List[Dict], commentary: dict = None,
                 eps_lookup: Dict[str, dict] = None) -> str:
    issuer_commentary = (commentary or {}).get('issuer_commentary') or {}
    eps_lookup = eps_lookup or {}
    rows = []
    for idx, rec in enumerate(records[:12]):
        llm = issuer_commentary.get(rec['ticker_region']) or issuer_commentary.get(rec['ticker']) or {}
        guidance = str(llm.get('guidance') or rec['guidance']).strip().lower()
        guidance_label = guidance.title()
        guidance_text = llm.get('guidance_detail') or _detail_text(rec['guidance_items'], 'No direct guidance quote found in the selected semantic snippets.')
        pos_items = [{'content': x} for x in (llm.get('positive_bullets') or [])] or rec['pos']
        neg_items = [{'content': x} for x in (llm.get('negative_bullets') or [])] or rec['neg']
        pos_text = llm.get('drivers') or _detail_text(rec['pos'][:2], 'No driver quote found.')
        neg_text = llm.get('headwinds') or _detail_text(rec['neg'][:2], 'No headwind quote found.')
        tkr = esc(rec['ticker_region'])
        guide_key = f'issuer:{tkr}:guide' if rec.get('guide') else f'issuer:{tkr}:all'
        eps_rec = eps_lookup.get(rec['ticker_region']) or {}
        eps_html = _eps_cell(eps_rec)
        sales_html = _sales_cell(eps_rec)
        rows.append(f"""<tr class="ern-issuer-row" data-idx="{idx}" onclick="toggleErnIssuer({idx})">
            <td class="ern-tk-col"><span class="ern-tk-pill">{esc(rec['ticker'])}</span><span class="ern-co-mini">{esc(rec['name'])}</span></td>
            <td class="ern-date-col">{esc(_date_label(rec['call_date']))}</td>
            <td class="ern-eps-col">{eps_html}</td>
            <td class="ern-sales-col">{sales_html}</td>
            <td class="ern-guide-col ern-hover-trigger" data-ev-key="{guide_key}"><span class="ern-guide-pill {esc(guidance)}">{esc(guidance_label)}</span></td>
            <td class="ern-pos-col ern-hover-trigger" data-ev-key="issuer:{tkr}:pos">{_bullets_html(pos_items, 'pos')}</td>
            <td class="ern-neg-col ern-hover-trigger" data-ev-key="issuer:{tkr}:neg">{_bullets_html(neg_items, 'neg')}</td>
            <td class="ern-expand-col"><span class="ern-expand-icon">+</span></td>
        </tr>
        <tr class="ern-issuer-detail" id="ern-detail-{idx}" hidden>
            <td colspan="8"><div class="ern-detail-grid">
                <div class="ern-detail-block full ern-hover-trigger" data-ev-key="{guide_key}"><div class="ern-detail-eyebrow">Guidance</div><div class="ern-detail-body">{esc(guidance_text)}</div></div>
                <div class="ern-detail-block ern-hover-trigger" data-ev-key="issuer:{tkr}:pos"><div class="ern-detail-eyebrow pos">Drivers</div><div class="ern-detail-body">{esc(pos_text)}</div></div>
                <div class="ern-detail-block ern-hover-trigger" data-ev-key="issuer:{tkr}:neg"><div class="ern-detail-eyebrow neg">Headwinds</div><div class="ern-detail-body">{esc(neg_text)}</div></div>
                <div class="ern-detail-block ern-hover-trigger" data-ev-key="issuer:{tkr}:all"><div class="ern-detail-eyebrow">Geographies</div><div class="ern-detail-body">{esc(llm.get('geographies') or 'No geography-specific synthesis available.')}</div></div>
                <div class="ern-detail-block ern-hover-trigger" data-ev-key="issuer:{tkr}:all"><div class="ern-detail-eyebrow">Segments</div><div class="ern-detail-body">{esc(llm.get('segments') or rec['sub_label'])}</div></div>
            </div></td>
        </tr>""")
    return ''.join(rows)


def render_module(industry: Industry, df_quotes: pd.DataFrame, commentary: dict = None,
                  eps_df=None) -> str:
    style = EARNINGS_CALLS_STYLE
    evidence_payload = ''
    if df_quotes is None or df_quotes.empty:
        body = """<div class="ern-module">
    <div class="ern-empty">No earnings call data cached yet. Run <code>python -m dash_reports.industry_tracker --industry consumer</code> to populate this section.</div>
</div>"""
    else:
        records = _issuer_records(df_quotes)
        chunk_meta = (commentary or {}).get('chunk_meta') or {}
        evidence_map = _build_evidence_map(records, chunk_meta)
        evidence_payload = ('<script>window.__ERN_EVIDENCE__ = '
                            + json.dumps(evidence_map, separators=(',', ':'), default=str)
                            + ';</script>')
        latest = pd.to_datetime(df_quotes['call_date']).max()
        earliest = pd.to_datetime(df_quotes['call_date']).min()
        date_range = (f'{earliest.strftime("%b %d")} \u2013 {latest.strftime("%b %d, %Y")}'
                      if pd.notna(latest) else '')
        eps_lookup = _eps_index(eps_df)
        eps_note = f' EPS and Sales surprise vs. consensus mean from FactSet Estimates ({len(eps_lookup)} issuers matched).' if eps_lookup else ''
        body = f"""<div class="ern-module">
        <div class="ern-section">
            <div class="ern-section-title">Earnings Summary &mdash; Cross-Universe Themes</div>
            <div class="ern-section-note">Themes ranked by frequency across {len(records)} FactSet CallStreet transcripts ({esc(date_range)}). Bar = number of calls citing the theme; takeaways are LLM-synthesized from cached evidence.</div>
            <div class="ern-theme-table-wrap"><table class="ern-theme-table">
                <thead><tr><th style="width:18%;">Theme</th><th style="width:22%;">Calls Citing</th><th>Takeaway</th></tr></thead>
                <tbody>{_theme_counts(records, commentary)}</tbody>
            </table></div>
        </div>
        <div class="ern-section">
            <div class="ern-section-title">Sub-Segment Performance</div>
            <div class="ern-section-note">Sub-segments mapped to public-equity comps tracker categories. Bar shows detected guidance mix across reporting issuers (guidance verbs classified from a dedicated semantic-search bucket).</div>
            <div class="ern-seg-legend">
                <div class="ern-seg-legend-item"><span class="ern-seg-legend-dot r"></span>Raised</div>
                <div class="ern-seg-legend-item"><span class="ern-seg-legend-dot m"></span>Maintained / Neutral</div>
                <div class="ern-seg-legend-item"><span class="ern-seg-legend-dot s"></span>Suspended</div>
                <div class="ern-seg-legend-item"><span class="ern-seg-legend-dot l"></span>Lowered</div>
            </div>
            <div class="ern-seg-grid">{_segment_cards(records, commentary)}</div>
        </div>
        <div class="ern-section">
            <div class="ern-section-title">Issuer-Level Detail &mdash; Recent Reporters</div>
            <div class="ern-section-note">{df_quotes['ticker_region'].nunique()} issuers and {len(df_quotes)} semantic transcript snippets from FactSet Vector API. Showing the 12 most recent reporters; click any row to expand synthesized detail.{esc(eps_note)}</div>
            <div class="ern-issuer-table-wrap"><table class="ern-issuer-table">
                <thead><tr><th style="width:15%;">Company</th><th style="width:5%;">Date</th><th style="width:10%;">EPS (Act / Est)</th><th style="width:11%;">Sales (Act / Est)</th><th style="width:8%;">Guidance</th><th style="width:24%;">Positives</th><th style="width:24%;">Negatives</th><th style="width:3%;"></th></tr></thead>
                <tbody>{_issuer_rows(records, commentary, eps_lookup)}</tbody>
            </table></div>
        </div>
        <div class="ern-source">Source: FactSet CallStreet transcripts via FactSet Vector API. EPS and Sales surprise vs. consensus from FactSet Estimates Snowflake (fe_v4.fe_basic_act_qf joined to fe_basic_conh_qf as of publication date; sales in reporting-currency millions). Commentary is LLM-synthesized from cached transcript evidence and refreshed with the daily tracker process.</div>
    </div>"""

    return f"""{style}
    {body}
    {evidence_payload}
    {EARNINGS_CALLS_SCRIPT}
"""


def render(industry: Industry, df_quotes: pd.DataFrame, commentary: dict = None,
           eps_df=None) -> str:
    return render_module(industry, df_quotes, commentary, eps_df=eps_df)
