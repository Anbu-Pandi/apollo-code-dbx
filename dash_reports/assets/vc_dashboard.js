(function () {
    const home = '/vcnews/default';
    const valid = ['funding', 'firms', 'deals', 'geo', 'portfolio', 'relationships', 'news', 'sources'];
    const isDashboard = () => /^\/vc-dashboard\/?$/.test(window.location.pathname);
    function restoreLocation() {
        if (!isDashboard()) return;
        const url = new URL(window.location.href);
        const selected = url.searchParams.get('tab');
        window.dash_clientside.set_props('vc-tabs', {value: valid.includes(selected) ? selected : 'funding'});
        window.dash_clientside.set_props('vc-search', {value: url.searchParams.get('q') || ''});
        window.dash_clientside.set_props('vc-min-size', {value: Number(url.searchParams.get('min') || 0)});
        window.dash_clientside.set_props('vc-window', {value: Number(url.searchParams.get('window') || 90)});
        window.dash_clientside.set_props('vc-apollo', {value: url.searchParams.get('apollo') === '1' ? ['apollo'] : []});
        window.dash_clientside.set_props('vc-activity-sort', {value: url.searchParams.get('sort') || 'deals'});
        const frame = document.getElementById('vc-news-frame');
        if (selected === 'news' && frame && framePath(frame) !== selectedPage()) {
            frame.src = selectedPage();
        }
    }
    window.addEventListener('popstate', restoreLocation);
    document.addEventListener('click', function (event) {
        const link = event.target.closest('a[data-vc-news]');
        if (!link || !isDashboard() || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const url = new URL(window.location.href);
        url.searchParams.set('tab', 'news');
        url.searchParams.set('news_page', link.dataset.vcNews);
        url.searchParams.delete('entity');
        url.searchParams.delete('name');
        if (url.href !== window.location.href) window.history.pushState(window.history.state, '', url);
        window.dash_clientside.set_props('vc-tabs', {value: 'news'});
        window.dash_clientside.set_props('vc-news-frame', {src: selectedPage()});
        document.getElementById('vc-tabs').scrollIntoView({block: 'start'});
    });
    function newsPath(value) {
        if (!value) return home;
        try {
            const path = value.startsWith('/') ? value : '/vcnews/' + value;
            const url = new URL(path, window.location.origin);
            return url.origin === window.location.origin && url.pathname.startsWith('/vcnews/')
                ? url.pathname + url.search + url.hash : home;
        } catch (_) { return home; }
    }
    function selectedPage() {
        return newsPath(new URL(window.location.href).searchParams.get('news_page'));
    }
    function framePath(frame) {
        try {
            const loc = frame.contentWindow.location;
            return loc.origin === window.location.origin ? loc.pathname + loc.search + loc.hash : null;
        } catch (_) { return null; }
    }
    document.addEventListener('load', function (event) {
        const frame = event.target;
        if (frame.id !== 'vc-news-frame' || !isDashboard()) return;
        const path = framePath(frame);
        if (!path || !path.startsWith('/vcnews/')) return;
        const url = new URL(window.location.href);
        url.searchParams.set('news_page', path.slice('/vcnews/'.length));
        // Iframe navigation already creates a browser history entry.
        window.history.replaceState(window.history.state, '', url);
        const current = document.getElementById('vc-news-current');
        const open = document.getElementById('vc-news-open');
        const heading = frame.contentDocument && frame.contentDocument.querySelector('article h1');
        let label = heading ? heading.textContent.trim() : decodeURIComponent(path.split('?')[0].split('/').pop()).replace(/_/g, ' ');
        if (/^\/vcnews\/default(?:\/page\/today)?(?:[?#]|$)/.test(path)) label = 'Today';
        if (current) { current.textContent = label; current.title = label; }
        if (open) open.href = path;
    }, true);
window.dash_clientside = Object.assign({}, window.dash_clientside, {
    vcDashboard: {
        syncTab: function (tab, search, minimum, days, apollo, sort) {
            if (!valid.includes(tab) || !isDashboard()) {
                return window.dash_clientside.no_update;
            }
            const url = new URL(window.location.href);
            const old = url.href;
            if (url.searchParams.has('entity') && url.searchParams.has('name')) {
                const dimension = {company: 'companies', investor: 'investors', deal: 'deals'}[url.searchParams.get('entity')];
                if (dimension) {
                    const slug = url.searchParams.get('name').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 80) || 'untitled';
                    url.searchParams.set('news_page', 'default/page/' + dimension + '/' + slug);
                }
                url.searchParams.delete('entity'); url.searchParams.delete('name');
            }
            const values = {q: search, min: minimum, window: days, apollo: apollo && (apollo.includes('apollo') ? '1' : ''), sort: sort};
            Object.entries(values).forEach(function ([key, value]) {
                if (value === undefined) return;
                if (value === null || value === '' || (key === 'min' && !value)) url.searchParams.delete(key);
                else url.searchParams.set(key, String(value));
            });
            if (url.searchParams.get('tab') !== tab || url.href !== old) {
                const initial = !url.searchParams.has('tab');
                url.searchParams.set('tab', tab);
                url.searchParams.delete('entity');
                url.searchParams.delete('name');
                window.history[initial ? 'replaceState' : 'pushState'](window.history.state, '', url);
            }
            return tab;
        },
        loadNews: function (tab, src) {
            return tab === 'news' && src !== selectedPage() ? selectedPage() : window.dash_clientside.no_update;
        },
        navigateNews: function (clicks) {
            const frame = document.getElementById('vc-news-frame');
            if (!frame) return window.dash_clientside.no_update;
            frame.src = home;
            return clicks;
        }
    }
});
})();
