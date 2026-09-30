(function () {
    'use strict';
    const storageKey = 'cdx-expiry-chart-order-v1';
    let saved = [];
    let dragging = null;
    let scheduled = false;
    try {
        const value = JSON.parse(localStorage.getItem(storageKey) || '[]');
        if (Array.isArray(value)) saved = [...new Set(value.filter(x => typeof x === 'string'))].slice(0, 200);
    } catch (error) { saved = []; }

    function cards() {
        const grid = document.getElementById('cdx-exp-chart-grid');
        return grid ? Array.from(grid.querySelectorAll('.cdx-orderable-chart')) : [];
    }
    function ordered() {
        return cards().filter(card => card.dataset.chartKey).sort((a, b) => {
            const ai = saved.indexOf(a.dataset.chartKey), bi = saved.indexOf(b.dataset.chartKey);
            if (ai >= 0 || bi >= 0) return (ai < 0 ? saved.length : ai) - (bi < 0 ? saved.length : bi);
            return Number(a.dataset.chartRank) - Number(b.dataset.chartRank);
        });
    }
    function apply() {
        ordered().forEach((card, index) => {
            if (card.style.order !== String(index)) card.style.order = String(index);
        });
    }
    function persist(order) {
        saved = order.concat(saved.filter(key => !order.includes(key))).slice(0, 200);
        let message = 'Chart order saved';
        try { localStorage.setItem(storageKey, JSON.stringify(saved)); }
        catch (error) { message = 'Order changed; browser storage is unavailable'; }
        apply();
        const status = document.getElementById('cdx-chart-order-status');
        if (status) status.textContent = message;
    }
    function move(source, target) {
        const order = ordered().map(card => card.dataset.chartKey);
        const from = order.indexOf(source), to = order.indexOf(target);
        if (from < 0 || to < 0 || from === to) return;
        order.splice(from, 1);
        order.splice(to, 0, source);
        persist(order);
    }
    document.addEventListener('dragstart', event => {
        const handle = event.target.closest('.cdx-chart-drag');
        if (!handle) return;
        const card = handle.closest('.cdx-orderable-chart');
        dragging = card.dataset.chartKey;
        event.dataTransfer.setData('text/plain', dragging);
        event.dataTransfer.effectAllowed = 'move';
        card.classList.add('cdx-chart-dragging');
    });
    document.addEventListener('dragover', event => {
        if (dragging && event.target.closest('.cdx-orderable-chart')) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
        }
    });
    document.addEventListener('drop', event => {
        const target = event.target.closest('.cdx-orderable-chart');
        if (!dragging || !target) return;
        event.preventDefault();
        move(dragging, target.dataset.chartKey);
    });
    document.addEventListener('dragend', () => {
        dragging = null;
        cards().forEach(card => card.classList.remove('cdx-chart-dragging'));
    });
    document.addEventListener('click', event => {
        if (event.target.closest('#cdx-chart-order-reset')) {
            saved = [];
            persist([]);
            return;
        }
        const button = event.target.closest('.cdx-chart-move');
        if (!button) return;
        const card = button.closest('.cdx-orderable-chart');
        const visible = ordered().filter(item => item.style.display !== 'none');
        const target = visible[visible.indexOf(card) + Number(button.dataset.direction)];
        if (target) move(card.dataset.chartKey, target.dataset.chartKey);
    });
    document.addEventListener('keydown', event => {
        if (!event.target.closest('.cdx-chart-drag') || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const card = event.target.closest('.cdx-orderable-chart');
        const visible = ordered().filter(item => item.style.display !== 'none');
        const target = visible[visible.indexOf(card) + (event.key === 'ArrowUp' ? -1 : 1)];
        if (target) move(card.dataset.chartKey, target.dataset.chartKey);
    });
    window.addEventListener('storage', event => {
        if (event.key !== storageKey) return;
        try {
            const value = JSON.parse(event.newValue || '[]');
            saved = Array.isArray(value) ? value.filter(x => typeof x === 'string').slice(0, 200) : [];
        } catch (error) { saved = []; }
        apply();
    });
    new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; apply(); });
    }).observe(document.documentElement, {childList: true, subtree: true, attributes: true,
                                         attributeFilter: ['data-chart-key', 'style']});
    apply();
}());
