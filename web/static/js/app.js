/**
 * Crypto Trading Bot - Frontend JavaScript
 */

// ============================================
// API Helpers
// ============================================

async function fetchAPI(endpoint, options = {}) {
    try {
        const response = await fetch(endpoint, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || `HTTP error ${response.status}`);
        }

        return data;
    } catch (error) {
        console.error(`API Error (${endpoint}):`, error);
        throw error;
    }
}

// ============================================
// Formatting Helpers
// ============================================

function formatCurrency(value, decimals = 2) {
    if (value === null || value === undefined) return '--';

    const num = parseFloat(value);
    if (isNaN(num)) return '--';

    // For small values (less than 1), show more decimals
    if (Math.abs(num) < 1 && Math.abs(num) > 0) {
        return '$' + num.toFixed(6);
    }

    return '$' + num.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

function formatNumber(value, decimals = 2) {
    if (value === null || value === undefined) return '--';

    const num = parseFloat(value);
    if (isNaN(num)) return '--';

    return num.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

function formatPercent(value, includeSign = true) {
    if (value === null || value === undefined) return '--';

    const num = parseFloat(value);
    if (isNaN(num)) return '--';

    const sign = includeSign && num >= 0 ? '+' : '';
    return sign + num.toFixed(2) + '%';
}

function formatDate(dateStr) {
    if (!dateStr) return '--';
    return new Date(dateStr).toLocaleString();
}

function formatTimeAgo(dateStr) {
    if (!dateStr) return '--';

    const date = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

// ============================================
// Toast Notifications
// ============================================

function showToast(title, message, type = 'info') {
    const toast = document.getElementById('toast');
    const toastTitle = document.getElementById('toastTitle');
    const toastBody = document.getElementById('toastBody');
    const toastIcon = document.getElementById('toastIcon');

    toastTitle.textContent = title;
    toastBody.textContent = message;

    // Set icon based on type
    const icons = {
        success: 'bi-check-circle text-success',
        danger: 'bi-x-circle text-danger',
        warning: 'bi-exclamation-triangle text-warning',
        info: 'bi-info-circle text-info'
    };
    toastIcon.className = 'bi me-2 ' + (icons[type] || icons.info);

    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
}

// ============================================
// Bot Control Functions
// ============================================

async function runCycle() {
    const btn = document.getElementById('runCycleBtn');
    const mainBtn = document.getElementById('runCycleBtnMain');

    // Disable buttons
    [btn, mainBtn].forEach(b => {
        if (b) {
            b.disabled = true;
            b.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Running...';
        }
    });

    try {
        const data = await fetchAPI('/api/run-cycle', { method: 'POST' });
        showToast('Trading Cycle', data.message, 'success');

        // Poll for completion
        pollBotStatus();

    } catch (error) {
        showToast('Error', error.message || 'Failed to start trading cycle', 'danger');

        // Re-enable buttons on error
        [btn, mainBtn].forEach(b => {
            if (b) {
                b.disabled = false;
                b.innerHTML = '<i class="bi bi-play-fill me-1"></i> Run Cycle';
            }
        });
    }
}

async function pollBotStatus() {
    const checkStatus = async () => {
        try {
            const data = await fetchAPI('/api/status');

            if (!data.running) {
                // Cycle completed
                const btn = document.getElementById('runCycleBtn');
                const mainBtn = document.getElementById('runCycleBtnMain');

                [btn, mainBtn].forEach(b => {
                    if (b) {
                        b.disabled = false;
                        b.innerHTML = '<i class="bi bi-play-fill me-1"></i> Run Cycle';
                    }
                });

                showToast('Cycle Complete', 'Trading cycle finished successfully', 'success');

                // Refresh dashboard data
                if (typeof loadDashboard === 'function') {
                    loadDashboard();
                }

                return;
            }

            // Still running, check again
            setTimeout(checkStatus, 2000);

        } catch (error) {
            console.error('Error polling status:', error);
        }
    };

    setTimeout(checkStatus, 1000);
}

// ============================================
// Update Functions
// ============================================

function updateLastUpdate() {
    const el = document.getElementById('lastUpdate');
    if (el) {
        el.textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }
}

function updateBotStatus(running) {
    const indicator = document.getElementById('botStatusIndicator');
    const text = document.getElementById('botStatusText');
    const sidebarStatus = document.querySelector('.sidebar-footer .status-indicator');
    const sidebarText = document.querySelector('.sidebar-footer .status-text');

    if (running) {
        if (indicator) indicator.className = 'status-indicator status-running me-2';
        if (text) text.textContent = 'Running...';
        if (sidebarStatus) sidebarStatus.className = 'status-indicator status-running';
        if (sidebarText) sidebarText.textContent = 'Running...';
    } else {
        if (indicator) indicator.className = 'status-indicator status-idle me-2';
        if (text) text.textContent = 'Idle';
        if (sidebarStatus) sidebarStatus.className = 'status-indicator status-idle';
        if (sidebarText) sidebarText.textContent = 'Idle';
    }
}

// ============================================
// Real-time Updates (SSE)
// ============================================

let eventSource = null;

function connectStream() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource('/api/stream');

    eventSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'update') {
                // Update prices if on dashboard
                if (typeof updateDashboardPrices === 'function') {
                    updateDashboardPrices(data.prices);
                }

                // Update bot status
                updateBotStatus(data.bot_running);
            }
        } catch (error) {
            console.error('Error processing stream data:', error);
        }
    };

    eventSource.onerror = function(error) {
        console.error('SSE Error:', error);
        // Reconnect after delay
        setTimeout(connectStream, 5000);
    };
}

// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    const tooltips = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltips.forEach(t => new bootstrap.Tooltip(t));

    // Connect to SSE stream (optional, can be resource intensive)
    // connectStream();

    // Update bot status periodically
    setInterval(async () => {
        try {
            const data = await fetchAPI('/api/status');
            updateBotStatus(data.running);
        } catch (error) {
            console.error('Error updating status:', error);
        }
    }, 10000);
});

// ============================================
// Chart Helpers (for future use)
// ============================================

function createLineChart(ctx, labels, data, label, color = '#0d6efd') {
    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: label,
                data: data,
                borderColor: color,
                backgroundColor: color + '20',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                x: {
                    grid: {
                        color: '#2d3238'
                    },
                    ticks: {
                        color: '#adb5bd'
                    }
                },
                y: {
                    grid: {
                        color: '#2d3238'
                    },
                    ticks: {
                        color: '#adb5bd'
                    }
                }
            }
        }
    });
}

function createPieChart(ctx, labels, data, colors) {
    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: colors,
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#adb5bd'
                    }
                }
            }
        }
    });
}
