(function() {
    'use strict';

    const REFRESH_INTERVAL = 60;
    let countdown = REFRESH_INTERVAL;
    let countdownTimer = null;
    let isLoading = false;
    let isUpdating = false;

    // DOM Elements
    const $ = (sel) => document.querySelector(sel);
    const containerList = $('#containerList');
    const loadingState = $('#loadingState');
    const emptyState = $('#emptyState');
    const errorState = $('#errorState');
    const refreshBtn = $('#refreshBtn');
    const updateAllBtn = $('#updateAllBtn');
    const retryBtn = $('#retryBtn');
    const lastCheck = $('#lastCheck');
    const countdownText = $('#countdownText');
    const countdownCircle = $('#countdownCircle');
    const totalCount = $('#totalCount');
    const updatesCount = $('#updatesCount');
    const currentCount = $('#currentCount');
    const errorsCount = $('#errorsCount');

    // Log Modal Elements
    const logModal = $('#logModal');
    const logModalBackdrop = $('#logModalBackdrop');
    const logModalTitle = $('#logModalTitle');
    const logModalBody = $('#logModalBody');
    const logModalClose = $('#logModalClose');
    const logModalFooter = $('#logModalFooter');
    const logModalCloseBtn = $('#logModalCloseBtn');
    const logProgressBar = $('#logProgressBar');

    const CIRCUMFERENCE = 2 * Math.PI * 12; // r=12

    // --- API ---
    async function fetchContainers(forceRefresh = false) {
        if (isLoading) return;
        isLoading = true;
        showLoading();

        try {
            const url = forceRefresh ? '/api/refresh' : '/api/containers';
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            render(data);
            updateLastCheck();
            hideAllStates();
            if (data.containers.length === 0) {
                showEmpty();
            }
        } catch (err) {
            console.error('Fetch error:', err);
            showError(err.message);
        } finally {
            isLoading = false;
            resetCountdown();
        }
    }

    // --- Streaming Update ---
    async function streamUpdate(url, title) {
        if (isUpdating) return;
        isUpdating = true;
        openLogModal(title);

        try {
            const res = await fetch(url, { method: 'POST' });
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const event = JSON.parse(line.slice(6));
                            handleLogEvent(event);
                        } catch (e) { /* skip malformed */ }
                    }
                }
            }
        } catch (err) {
            addLogEntry('error', `Verbindungsfehler: ${err.message}`);
            setProgressState('error');
        }

        completeLogModal();
        isUpdating = false;
        setTimeout(() => fetchContainers(true), 1000);
    }

    function handleLogEvent(event) {
        switch (event.type) {
            case 'log':
                addLogEntry(event.icon || 'info', event.message);
                pulseProgress();
                break;
            case 'pull_progress':
                addLogEntry('layer', event.message);
                break;
            case 'container_start':
                addContainerDivider(event.container, event.index, event.total);
                setProgressValue((event.index - 1) / event.total * 100);
                break;
            case 'complete':
                addLogEntry(event.success ? 'success' : 'error', event.message);
                setProgressState(event.success ? 'success' : 'error');
                if (event.reload) {
                    addLogEntry('info', 'Seite wird in 5 Sekunden neu geladen…');
                    setTimeout(() => window.location.reload(), 5000);
                }
                break;
            case 'all_complete':
                addLogEntry(event.success ? 'success' : 'error', event.message);
                setProgressValue(100);
                setProgressState(event.success ? 'success' : 'error');
                break;
        }
    }

    // --- Log Modal ---
    function openLogModal(title) {
        logModalTitle.textContent = title;
        logModalBody.innerHTML = '';
        logModalFooter.classList.add('hidden');
        logProgressBar.style.width = '0%';
        logProgressBar.className = 'log-modal-progress-bar indeterminate';
        logModal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }

    function completeLogModal() {
        logProgressBar.classList.remove('indeterminate');
        logModalFooter.classList.remove('hidden');
    }

    function closeLogModal() {
        logModal.classList.add('hidden');
        document.body.style.overflow = '';
    }

    function addLogEntry(icon, message) {
        const entry = document.createElement('div');
        const isLayer = icon === 'layer';
        entry.className = `log-entry${isLayer ? ' log-layer' : ''}`;

        const time = new Date().toLocaleTimeString('de-DE', {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });

        const iconMap = {
            info: { symbol: '\u25CF', cls: 'info' },      // ●
            success: { symbol: '\u2713', cls: 'success' }, // ✓
            error: { symbol: '\u2717', cls: 'error' },     // ✗
            layer: { symbol: '\u2502', cls: 'layer' },     // │
        };
        const ic = iconMap[icon] || iconMap.info;

        entry.innerHTML = `
            <span class="log-time">${time}</span>
            <span class="log-icon ${ic.cls}">${ic.symbol}</span>
            <span class="log-message">${escapeHTML(message)}</span>
        `;

        logModalBody.appendChild(entry);
        logModalBody.scrollTop = logModalBody.scrollHeight;
    }

    function addContainerDivider(name, index, total) {
        const divider = document.createElement('div');
        divider.className = 'log-divider';
        divider.innerHTML = `
            <span class="log-divider-line"></span>
            <span class="log-divider-text">${escapeHTML(name)} (${index}/${total})</span>
            <span class="log-divider-line"></span>
        `;
        logModalBody.appendChild(divider);
        logModalBody.scrollTop = logModalBody.scrollHeight;
    }

    function pulseProgress() {
        // Keep indeterminate animation running
    }

    function setProgressValue(percent) {
        logProgressBar.classList.remove('indeterminate');
        logProgressBar.style.width = `${percent}%`;
    }

    function setProgressState(state) {
        logProgressBar.classList.remove('indeterminate');
        if (state === 'success') {
            logProgressBar.style.width = '100%';
            logProgressBar.classList.add('success');
        } else if (state === 'error') {
            logProgressBar.classList.add('error');
        }
    }

    // Modal event listeners
    logModalClose.addEventListener('click', closeLogModal);
    logModalCloseBtn.addEventListener('click', closeLogModal);
    logModalBackdrop.addEventListener('click', closeLogModal);

    // --- Render ---
    function render(data) {
        renderSummary(data.summary);
        renderContainers(data.containers);

        if (data.summary.updates_available > 0) {
            updateAllBtn.classList.remove('hidden');
        } else {
            updateAllBtn.classList.add('hidden');
        }
    }

    function renderSummary(summary) {
        animateValue(totalCount, summary.total);
        animateValue(updatesCount, summary.updates_available);
        animateValue(currentCount, summary.up_to_date);
        animateValue(errorsCount, summary.errors);
    }

    function animateValue(el, target) {
        const current = parseInt(el.textContent) || 0;
        if (current === target) {
            el.textContent = target;
            return;
        }
        const duration = 400;
        const start = performance.now();
        function step(now) {
            const progress = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.round(current + (target - current) * eased);
            if (progress < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    function renderContainers(containers) {
        containerList.innerHTML = '';
        containers.forEach((c, i) => {
            const card = document.createElement('div');
            card.className = `container-card ${getStatusClass(c)}`;
            card.dataset.id = c.id;
            card.style.animationDelay = `${0.05 * i}s`;
            card.innerHTML = buildCardHTML(c);
            containerList.appendChild(card);
        });
    }

    function getStatusClass(c) {
        if (c.error) return 'status-error';
        if (c.update_available === true) return 'status-update';
        if (c.update_available === false) return 'status-current';
        return 'status-unknown';
    }

    function buildCardHTML(c) {
        const stateClass = c.state === 'running' ? 'running' : '';
        const ports = c.ports && c.ports.length > 0
            ? c.ports.join(', ')
            : '\u2013';
        const startedAt = c.started_at ? formatRelativeTime(c.started_at) : '\u2013';

        let digestHTML = '';
        if (c.local_digest) {
            const short = c.local_digest.replace('sha256:', '').substring(0, 12);
            digestHTML = `<span class="digest-info" title="${c.local_digest}">sha256:${short}</span>`;
        }

        return `
            <div class="container-info">
                <div class="container-header">
                    <span class="container-name">${escapeHTML(c.name)}</span>
                    <span class="container-state ${stateClass}">${escapeHTML(c.state)}</span>
                </div>
                <div class="container-image" title="${escapeHTML(c.image)}">${escapeHTML(c.image)}</div>
                <div class="container-meta">
                    <span class="meta-item">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                        ${startedAt}
                    </span>
                    <span class="meta-item">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>
                        ${escapeHTML(ports)}
                    </span>
                </div>
            </div>
            <div class="container-status">
                ${buildUpdateBadge(c)}
                ${c.update_available ? buildUpdateButton(c) : ''}
                ${digestHTML}
            </div>
        `;
    }

    function buildUpdateBadge(c) {
        if (c.error) {
            return `<span class="update-badge error" title="${escapeHTML(c.error)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Fehler
            </span>`;
        }
        if (c.update_available === true) {
            return `<span class="update-badge update-available">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Update verfügbar
            </span>`;
        }
        if (c.update_available === false) {
            return `<span class="update-badge up-to-date">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                Aktuell
            </span>`;
        }
        return `<span class="update-badge unknown">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            Unbekannt
        </span>`;
    }

    function buildUpdateButton(c) {
        const safeId = c.id.replace(/'/g, "\\'");
        const safeName = c.name.replace(/'/g, "\\'");
        return `<button class="btn-update" onclick="window.__updateContainer('${safeId}', '${safeName}')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            Aktualisieren
        </button>`;
    }

    // Expose for inline onclick
    window.__updateContainer = function(id, name) {
        streamUpdate(`/api/containers/${id}/update`, `${name} aktualisieren`);
    };

    // --- Helpers ---
    function escapeHTML(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function formatRelativeTime(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHrs = Math.floor(diffMin / 60);
        const diffDays = Math.floor(diffHrs / 24);

        if (diffSec < 60) return 'gerade eben';
        if (diffMin < 60) return `vor ${diffMin} Min.`;
        if (diffHrs < 24) return `vor ${diffHrs} Std.`;
        if (diffDays < 30) return `vor ${diffDays} Tag${diffDays === 1 ? '' : 'en'}`;
        return date.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
    }

    function updateLastCheck() {
        const now = new Date();
        const time = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        lastCheck.textContent = `Letzte Prüfung: ${time}`;
    }

    // --- UI States ---
    function showLoading() {
        loadingState.classList.remove('hidden');
        emptyState.classList.add('hidden');
        errorState.classList.add('hidden');
        containerList.innerHTML = '';
        refreshBtn.classList.add('loading');
    }

    function hideAllStates() {
        loadingState.classList.add('hidden');
        emptyState.classList.add('hidden');
        errorState.classList.add('hidden');
        refreshBtn.classList.remove('loading');
    }

    function showEmpty() {
        emptyState.classList.remove('hidden');
    }

    function showError(msg) {
        hideAllStates();
        errorState.classList.remove('hidden');
        const errorMessage = $('#errorMessage');
        errorMessage.textContent = msg || 'Verbindung zum Server fehlgeschlagen';
    }

    // --- Countdown ---
    function resetCountdown() {
        countdown = REFRESH_INTERVAL;
        updateCountdownDisplay();
        clearInterval(countdownTimer);
        countdownTimer = setInterval(() => {
            countdown--;
            updateCountdownDisplay();
            if (countdown <= 0) {
                clearInterval(countdownTimer);
                fetchContainers(false);
            }
        }, 1000);
    }

    function updateCountdownDisplay() {
        countdownText.textContent = countdown;
        const offset = CIRCUMFERENCE * (1 - countdown / REFRESH_INTERVAL);
        countdownCircle.style.strokeDashoffset = offset;
    }

    // --- Events ---
    refreshBtn.addEventListener('click', () => fetchContainers(true));
    updateAllBtn.addEventListener('click', () => {
        streamUpdate('/api/update-all', 'Alle Container aktualisieren');
    });
    retryBtn.addEventListener('click', () => fetchContainers(false));

    // --- Init ---
    fetchContainers(false);
})();
