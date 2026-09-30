/* eslint-disable */
let ltCharts = [];
let ltCurrentMult = 'ntm';

function toggleErnIssuer(idx) {
    const row = document.querySelector('.ern-module .ern-issuer-row[data-idx="' + idx + '"]');
    const detail = document.getElementById('ern-detail-' + idx);
    if (!row || !detail) return;
    const willExpand = detail.hasAttribute('hidden');
    if (willExpand) {
        detail.removeAttribute('hidden');
        row.classList.add('expanded');
        const icon = row.querySelector('.ern-expand-icon');
        if (icon) icon.textContent = '\u2212';
    } else {
        detail.setAttribute('hidden', '');
        row.classList.remove('expanded');
        const icon = row.querySelector('.ern-expand-icon');
        if (icon) icon.textContent = '+';
    }
}

function scrollToSector(anchorId) {
    const target = document.getElementById(anchorId);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.style.transition = 'background 0.3s ease';
    const cells = target.querySelectorAll('td');
    cells.forEach(c => {
        const orig = c.style.background;
        c.style.background = '#FFF8E0';
        setTimeout(() => { c.style.background = orig; }, 1200);
    });
}

function scrollToCreditSection(id) {
    const el = document.getElementById(id);
    if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
}

function applyGradient(cellClass) {
    const cells = document.querySelectorAll('.summary-table td.' + cellClass);
    const values = [];
    cells.forEach(td => {
        const raw = parseFloat(td.textContent.replace(/[%x,$+]/g, ''));
        values.push(isNaN(raw) ? null : raw);
    });
    const valid = values.filter(v => v !== null);
    if (valid.length === 0) return;
    const min = Math.min(...valid), max = Math.max(...valid), range = max - min;
    const tR = 99, tG = 190, tB = 123;
    cells.forEach((td, i) => {
        if (values[i] === null) return;
        const t = range === 0 ? 1 : (values[i] - min) / range;
        const r = Math.round(255 + (tR - 255) * t);
        const g = Math.round(255 + (tG - 255) * t);
        const b = Math.round(255 + (tB - 255) * t);
        td.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
    });
}

function applyDetailGradients(segCount) {
    for (let i = 0; i < segCount; i++) {
        ['hi', 'lo', 'tev', 'ec'].forEach(group => {
            const cls = 'grad-' + group + '-seg-' + i;
            const cells = document.querySelectorAll('.detail-table[data-seg="' + i + '"] td.' + cls);
            const values = [];
            cells.forEach(td => {
                const raw = parseFloat(td.textContent.replace(/[%x,$+]/g, ''));
                values.push(isNaN(raw) ? null : raw);
            });
            const valid = values.filter(v => v !== null);
            if (valid.length === 0) return;
            const min = Math.min(...valid), max = Math.max(...valid), range = max - min;
            const tR = 99, tG = 190, tB = 123;
            cells.forEach((td, idx) => {
                if (values[idx] === null) return;
                const t = range === 0 ? 1 : (values[idx] - min) / range;
                const r = Math.round(255 + (tR - 255) * t);
                const g = Math.round(255 + (tG - 255) * t);
                const b = Math.round(255 + (tB - 255) * t);
                td.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
            });
        });
    }
}

function showTab(tabId) {
    document.querySelectorAll('.tab-section').forEach(s => {
        s.hidden = (s.id !== 'tab-' + tabId);
    });
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tabId);
    });
    try {
        if (history.replaceState) {
            history.replaceState(null, '', '#' + tabId);
        }
    } catch (e) { /* sandboxed iframe */ }
    if (tabId === 'lt-historical') {
        setTimeout(ensureLtRendered, 30);
    }
}

function renderLtCharts(mult) {
    if (typeof LT_DATA === 'undefined' || !LT_DATA) return;
    const dates = LT_DATA.dates;
    const allCats = LT_DATA.categories;
    const series = mult === 'ntm' ? LT_DATA.ntm : LT_DATA.ltm;
    const avgs = mult === 'ntm' ? LT_DATA.avg_ntm : LT_DATA.avg_ltm;

    allCats.forEach((cat, i) => {
        const canvas = document.getElementById('lt-chart-' + i);
        if (!canvas) return;
        const data = series[cat] || [];
        const avg = avgs[cat];
        if (ltCharts[i]) ltCharts[i].destroy();
        const avgLine = data.map(() => avg);

        ltCharts[i] = new Chart(canvas, {
            type: 'line',
            data: {
                labels: dates,
                datasets: [
                    {
                        label: mult.toUpperCase() + ' Multiple',
                        data: data,
                        borderColor: '#007B63',
                        backgroundColor: 'rgba(0, 123, 99, 0.06)',
                        borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4,
                        pointHoverBackgroundColor: '#007B63',
                        tension: 0.15, fill: true, spanGaps: true,
                    },
                    {
                        label: '10-Yr Avg',
                        data: avgLine,
                        borderColor: '#93867A',
                        borderWidth: 1.5, borderDash: [5, 4],
                        pointRadius: 0, pointHoverRadius: 0, fill: false,
                    },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        display: true, position: 'bottom',
                        labels: { font: { family: 'Calibri, sans-serif', size: 10 },
                                  color: '#55585A', boxWidth: 18, boxHeight: 2,
                                  padding: 8, usePointStyle: false },
                    },
                    tooltip: {
                        backgroundColor: 'rgba(255, 255, 255, 0.97)',
                        titleColor: '#000', bodyColor: '#000',
                        borderColor: '#CAC2BA', borderWidth: 1, padding: 8,
                        titleFont: { family: 'Calibri', size: 11, weight: 'bold' },
                        bodyFont: { family: 'Calibri', size: 11 },
                        callbacks: {
                            label: function(ctx) {
                                const v = ctx.parsed.y;
                                if (v === null || v === undefined) return ctx.dataset.label + ': --';
                                return ctx.dataset.label + ': ' + v.toFixed(1) + 'x';
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: { font: { family: 'Calibri', size: 9 }, color: '#55585A',
                                 maxRotation: 0, autoSkip: true, maxTicksLimit: 6,
                                 callback: function(val) {
                                     const d = this.getLabelForValue(val);
                                     if (typeof d === 'string' && d.length >= 4) return d.substring(0, 4);
                                     return d;
                                 } },
                        grid: { display: false },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { font: { family: 'Calibri', size: 9 }, color: '#55585A',
                                 callback: function(v) { return v + 'x'; } },
                        grid: { color: 'rgba(202, 194, 186, 0.3)' },
                    },
                },
            },
        });
    });

    document.querySelectorAll('.lt-stat-cur').forEach(el => {
        const v = mult === 'ntm' ? el.dataset.curNtm : el.dataset.curLtm;
        el.textContent = v ? parseFloat(v).toFixed(1) + 'x' : '--';
    });
    document.querySelectorAll('.lt-stat-avg').forEach(el => {
        const v = mult === 'ntm' ? el.dataset.avgNtm : el.dataset.avgLtm;
        el.textContent = v ? parseFloat(v).toFixed(1) + 'x' : '--';
    });
    document.querySelectorAll('.lt-period-val').forEach(el => {
        const v = mult === 'ntm' ? el.dataset.valNtm : el.dataset.valLtm;
        el.textContent = v ? parseFloat(v).toFixed(1) + 'x' : '--';
    });
    document.querySelectorAll('.lt-sum-val').forEach(el => {
        const v = mult === 'ntm' ? el.dataset.valNtm : el.dataset.valLtm;
        el.textContent = v ? parseFloat(v).toFixed(1) + 'x' : '--';
    });
    document.querySelectorAll('.lt-sum-pd').forEach(el => {
        const v = mult === 'ntm' ? el.dataset.pdNtm : el.dataset.pdLtm;
        el.classList.remove('pos', 'neg');
        if (v === '' || v === undefined) { el.textContent = '--'; return; }
        const n = parseFloat(v);
        const sign = n >= 0 ? '+' : '';
        el.textContent = sign + n.toFixed(1) + 'x';
        el.classList.add(n >= 0 ? 'pos' : 'neg');
    });
}

let ltRendered = false;
function ensureLtRendered() {
    if (!ltRendered) {
        renderLtCharts(ltCurrentMult);
        ltRendered = true;
    }
}

function toggleWwrCard(idx) {
    const card = document.querySelector('.wwr-card[data-card-idx="' + idx + '"]');
    if (!card) return;
    const body = card.querySelector('.wwr-card-body');
    const icon = card.querySelector('.wwr-toggle-icon');
    if (!body) return;
    const willExpand = body.hasAttribute('hidden');
    if (willExpand) {
        body.removeAttribute('hidden');
        if (icon) icon.textContent = '\u2212';
        card.classList.add('expanded');
    } else {
        body.setAttribute('hidden', '');
        if (icon) icon.textContent = '+';
        card.classList.remove('expanded');
    }
}

function bootstrapPage(segCount) {
    applyGradient('grad-52w');
    applyGradient('grad-tev');
    applyGradient('grad-ec');
    applyDetailGradients(segCount || 0);

    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => showTab(btn.dataset.tab));
    });

    const hash = (window.location.hash || '').replace('#', '');
    const valid = ['macro', 'public-comps', 'credit-comps', 'lt-historical', 'wwr', 'wwo'];
    if (valid.includes(hash)) showTab(hash);

    document.querySelectorAll('.lt-toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const m = btn.dataset.mult;
            if (m === ltCurrentMult) return;
            ltCurrentMult = m;
            document.querySelectorAll('.lt-toggle-btn').forEach(b =>
                b.classList.toggle('active', b.dataset.mult === m));
            renderLtCharts(m);
        });
    });

    if ((window.location.hash || '').replace('#', '') === 'lt-historical') {
        setTimeout(ensureLtRendered, 30);
    }

    document.querySelectorAll('.lt-summary-table thead th').forEach((th, idx) => {
        th.addEventListener('click', () => {
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const asc = th.dataset.sort !== 'asc';
            table.querySelectorAll('th').forEach(h => delete h.dataset.sort);
            th.dataset.sort = asc ? 'asc' : 'desc';
            rows.sort((a, b) => {
                const ca = a.cells[idx], cb = b.cells[idx];
                let va = ca ? ca.textContent.trim() : '';
                let vb = cb ? cb.textContent.trim() : '';
                if (idx === 0) return asc ? va.localeCompare(vb) : vb.localeCompare(va);
                const na = parseFloat(va.replace(/[%x,+]/g, ''));
                const nb = parseFloat(vb.replace(/[%x,+]/g, ''));
                const fa = isNaN(na) ? -Infinity : na;
                const fb = isNaN(nb) ? -Infinity : nb;
                return asc ? fa - fb : fb - fa;
            });
            rows.forEach(r => tbody.appendChild(r));
        });
    });

    document.querySelectorAll('.detail-table th').forEach(th => {
        th.addEventListener('click', function() {
            const table = this.closest('table');
            const tbody = table.querySelector('tbody');
            const dataRows = Array.from(tbody.querySelectorAll('tr.data-row'));
            const summaryRows = Array.from(tbody.querySelectorAll('tr.summary-row'));
            const isNum = this.classList.contains('num');
            const asc = this.dataset.sort !== 'asc';
            table.querySelectorAll('th').forEach(h => delete h.dataset.sort);
            this.dataset.sort = asc ? 'asc' : 'desc';
            const sortColIndex = parseInt(this.dataset.sortCol);
            const colIndex = Array.from(this.parentNode.children).indexOf(this);
            const useColIndex = !isNaN(sortColIndex) ? sortColIndex : colIndex;
            dataRows.sort((a, b) => {
                let va = a.cells[useColIndex] ? a.cells[useColIndex].textContent.trim() : '';
                let vb = b.cells[useColIndex] ? b.cells[useColIndex].textContent.trim() : '';
                if (isNum) {
                    va = parseFloat(va.replace(/[%x,$+]/g, '')) || -Infinity;
                    vb = parseFloat(vb.replace(/[%x,$+]/g, '')) || -Infinity;
                }
                if (va < vb) return asc ? -1 : 1;
                if (va > vb) return asc ? 1 : -1;
                return 0;
            });
            dataRows.forEach(row => tbody.appendChild(row));
            summaryRows.forEach(row => tbody.appendChild(row));
        });
    });
}
