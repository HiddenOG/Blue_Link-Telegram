const WebApp = window.Telegram.WebApp;
WebApp.expand();

// DOM Elements
const loader = document.getElementById('loader');
const app = document.getElementById('app');
const errorView = document.getElementById('error-view');
const bizSelect = document.getElementById('biz-select');
const welcomeMsg = document.getElementById('welcome-msg');
const coinBalance = document.getElementById('coin-balance');
const closeBtn = document.getElementById('close-btn');
const editBtn = document.getElementById('edit-btn');
const editView = document.getElementById('edit-view');
const editForm = document.getElementById('edit-form');
const editBackBtn = document.getElementById('edit-back-btn');
const deleteBtn = document.getElementById('delete-btn');


// Stats Elements
const statRequests = document.getElementById('stat-requests');
const statClicks = document.getElementById('stat-clicks');
const statConversion = document.getElementById('stat-conversion');
const infoService = document.getElementById('info-service');
const infoLocation = document.getElementById('info-location');
const infoBoost = document.getElementById('info-boost');

let chart = null;
let currentData = null;
let userId = null; // Global user ID for API calls

// Photo editing state
let photoState = {
    existing: [],   // [{slot: 'photo_1', file_id: 'xxx'}, ...]
    removed: [],    // ['photo_1', 'photo_2'] - slots to set null
    newFiles: []    // [File, File] - new uploads
};

// Initialize
async function init() {
    try {
        WebApp.ready();
        const initData = WebApp.initData;

        // Check for fallback UID in URL (for Ngrok/Dev environments)
        const urlParams = new URLSearchParams(window.location.search);
        const fallbackUid = urlParams.get('uid');

        console.log("WebApp InitData length:", initData.length);
        console.log("Fallback UID:", fallbackUid);

        // Strict Check: if no initData AND no fallback UID
        if (!initData && !fallbackUid) {
            loader.classList.add('hidden');
            errorView.classList.remove('hidden');
            errorView.innerHTML = `
                <div class="error-container">
                    <span class="error-icon">⛔</span>
                    <h2>Access Denied</h2>
                    <p>You are trying to access this page outside of Telegram.</p>
                    <p style="font-size: 0.9rem; color: #888; margin-top: 10px;">
                        Please close this and open it using the <b>"📊 My Dashboard"</b> button inside the bot.
                    </p>
                </div>
            `;
            return;
        }



        // Setup user info
        const userDisplay = WebApp.initDataUnsafe.user;
        if (userDisplay) {
            welcomeMsg.innerText = `Hi, ${userDisplay.first_name || 'Business Owner'}!`;
            if (userDisplay.photo_url) {
                const img = document.getElementById('user-photo');
                img.src = userDisplay.photo_url;
                img.classList.remove('hidden');
            }
        }



        let apiUrl = `/api/dashboard?initData=${encodeURIComponent(initData || '')}`;
        if (fallbackUid) {
            apiUrl += `&user_id=${fallbackUid}`;
        }

        // Resolve userId for all subsequent API calls
        const user = WebApp.initDataUnsafe.user;
        userId = (user && user.id) ? user.id : fallbackUid;

        // Fetch data from API
        const response = await fetch(apiUrl, {
            headers: {
                'ngrok-skip-browser-warning': 'true'
            }
        });

        if (!response.ok) throw new Error('Failed to load dashboard data');

        currentData = await response.json();

        // Update greeting with API user_name if WebApp didn't provide one
        if (!userDisplay && currentData.user_name) {
            welcomeMsg.innerText = `Hi, ${currentData.user_name}!`;
        }

        renderDashboard(currentData);

        loader.classList.add('hidden');
        app.classList.remove('hidden');
    } catch (err) {
        console.error("Init Error:", err);

        let displayMsg = "Unable to load dashboard.";

        if (!navigator.onLine) {
            displayMsg = "⚠️ No Internet Connection. Please check your network.";
        } else if (err.message.includes("Failed to fetch")) {
            displayMsg = "⚠️ Connection Timeout. The server is taking too long to respond.";
        } else {
            // Use the actual error message if it's readable
            displayMsg = `⚠️ Error: ${err.message}`;
        }

        document.getElementById('error-msg').innerText = displayMsg;
        errorView.querySelector('h2').innerText = "Dashboard Error"; // Fix misleading "No business yet" title on errors

        // Diagnostic Data (Hidden by default, simplistic)
        // Diagnostic Data REMOVED as requested
        loader.classList.add('hidden');
        errorView.classList.remove('hidden');
    }
}

function renderDashboard(data) {
    // 1. Handle No Businesses (Empty State)
    if (!data.businesses || data.businesses.length === 0) {
        app.innerHTML = `
            <div style="text-align: center; padding: 40px 20px;">
                <div style="font-size: 60px; margin_bottom: 20px;">🏪</div>
                <h2 style="color: #fff; margin-bottom: 15px;">No Business Yet</h2>
                <p style="color: #ccc; margin-bottom: 30px;">
                    You need to register a business to access the dashboard analytics and features.
                </p>
                <button onclick="triggerRegistration()" style="
                    background: #248bcf;
                    color: white;
                    border: none;
                    padding: 12px 25px;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: bold;
                    cursor: pointer;
                    width: 100%;
                    max-width: 250px;
                ">
                    🏪 Register Your Business
                </button>
            </div>
        `;
        // Update Coin Balance even if no business
        coinBalance.innerText = data.coins || 0;
        return;
    }

    // Populate Selector
    bizSelect.innerHTML = '';
    data.businesses.forEach((biz, index) => {
        const option = document.createElement('option');
        option.value = index;
        option.innerText = biz.business_name;
        bizSelect.appendChild(option);
    });

    coinBalance.innerText = data.coins || 0;

    // Initial Business view
    updateBusinessView(0);
}

function updateBusinessView(index) {
    const biz = currentData.businesses[index];
    const analytics = biz.analytics || { requests: 0, whatsapp_clicks: 0, conversion: 0 };

    statRequests.innerText = analytics.requests;
    statClicks.innerText = analytics.whatsapp_clicks;
    statConversion.innerText = `${analytics.conversion}%`;

    infoService.innerText = biz.service || 'N/A';
    infoLocation.innerText = biz.location || 'N/A';

    const boost = biz.boost_status || { active: false, message: 'Not Boosted' };
    infoBoost.innerText = boost.message;
    infoBoost.className = `boost-badge ${boost.active ? 'active' : ''}`;

    renderChart(biz.weekly_stats || []);
}

function renderChart(weeklyData) {
    const ctx = document.getElementById('analyticsChart').getContext('2d');

    if (chart) chart.destroy();

    const labels = weeklyData.map(d => d.date);
    const requests = weeklyData.map(d => d.requests);
    const clicks = weeklyData.map(d => d.clicks);

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Requests',
                    data: requests,
                    borderColor: '#248bcf',
                    backgroundColor: 'rgba(36, 139, 207, 0.1)',
                    fill: true,
                    tension: 0.4
                },
                {
                    label: 'Clicks',
                    data: clicks,
                    borderColor: '#00ff00',
                    backgroundColor: 'rgba(0, 255, 0, 0.1)',
                    fill: true,
                    tension: 0.4
                }
            ]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } },
                x: { grid: { display: false } }
            }
        }
    });
}

// --- Navigation & Edit Logic ---

function showEditPage() {
    const biz = currentData.businesses[bizSelect.value];
    if (!biz) return;

    // Pre-fill form
    document.getElementById('edit-name').value = biz.business_name;
    document.getElementById('edit-service').value = biz.service;
    document.getElementById('edit-location').value = biz.location;
    document.getElementById('edit-phone').value = biz.phone;
    document.getElementById('edit-description').value = biz.description;

    // Load photos
    photoState = { existing: [], removed: [], newFiles: [] };
    ['photo_1', 'photo_2', 'photo_3'].forEach(slot => {
        if (biz[slot]) {
            photoState.existing.push({ slot, file_id: biz[slot] });
        }
    });
    renderPhotoManager();

    // Show view
    app.classList.add('hidden');
    editView.classList.remove('hidden');
}

function renderPhotoManager() {
    const grid = document.getElementById('photo-manager');
    grid.innerHTML = '';

    const addBtn = document.getElementById('add-photo-btn');
    const totalPhotos = photoState.existing.length + photoState.newFiles.length;

    // Show existing photos
    photoState.existing.forEach((photo, idx) => {
        const thumb = document.createElement('div');
        thumb.className = 'photo-thumb';
        thumb.innerHTML = `
            <img src="/api/photo?file_id=${encodeURIComponent(photo.file_id)}" alt="Photo">
            <button type="button" class="remove-photo" data-type="existing" data-index="${idx}">×</button>
        `;
        grid.appendChild(thumb);
    });

    // Show new uploads
    photoState.newFiles.forEach((file, idx) => {
        const thumb = document.createElement('div');
        thumb.className = 'photo-thumb';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        img.alt = 'New photo';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'remove-photo';
        btn.dataset.type = 'new';
        btn.dataset.index = idx;
        btn.textContent = '×';
        thumb.appendChild(img);
        thumb.appendChild(btn);
        grid.appendChild(thumb);
    });

    // Hide add button if at max
    addBtn.style.display = totalPhotos >= 3 ? 'none' : '';
}

function hideEditPage() {
    editView.classList.add('hidden');
    app.classList.remove('hidden');
}

async function saveBusinessChanges(e) {
    e.preventDefault();
    const biz = currentData.businesses[bizSelect.value];
    const formData = new FormData(editForm);
    const updates = Object.fromEntries(formData.entries());

    // Add photo removals (set to null)
    photoState.removed.forEach(slot => {
        updates[slot] = null;
    });

    loader.classList.remove('hidden');
    editView.classList.add('hidden');

    try {
        const initData = WebApp.initData;

        // Save text fields + photo removals
        const response = await fetch(`/api/business/update?initData=${encodeURIComponent(initData)}&business_id=${biz.id}&user_id=${userId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            },
            body: JSON.stringify(updates)
        });

        if (!response.ok) throw new Error('Failed to update business');

        // Upload new photos if any
        if (photoState.newFiles.length > 0) {
            const usedSlots = photoState.existing.map(p => p.slot);
            const availableSlots = ['photo_1', 'photo_2', 'photo_3'].filter(s => !usedSlots.includes(s));

            for (let i = 0; i < photoState.newFiles.length && i < availableSlots.length; i++) {
                const uploadForm = new FormData();
                uploadForm.append('photo', photoState.newFiles[i]);
                uploadForm.append('slot', availableSlots[i]);

                await fetch(`/api/business/upload-photo?initData=${encodeURIComponent(initData)}&business_id=${biz.id}&user_id=${userId}`, {
                    method: 'POST',
                    headers: { 'ngrok-skip-browser-warning': 'true' },
                    body: uploadForm
                });
            }
        }

        WebApp.showAlert('Business updated successfully! ✅');
        window.location.reload();
    } catch (err) {
        console.error(err);
        WebApp.showAlert('Error: ' + err.message);
        loader.classList.add('hidden');
        editView.classList.remove('hidden');
    }
}

async function deleteBusiness() {
    const biz = currentData.businesses[bizSelect.value];

    WebApp.showConfirm(`Are you sure you want to delete "${biz.business_name}"? This action cannot be undone. ⚠️`, async (confirmed) => {
        if (!confirmed) return;

        loader.classList.remove('hidden');
        editView.classList.add('hidden');

        try {
            const initData = WebApp.initData;
            const response = await fetch(`/api/business/delete?initData=${encodeURIComponent(initData)}&business_id=${biz.id}&user_id=${userId}`, {
                method: 'POST',
                headers: {
                    'ngrok-skip-browser-warning': 'true'
                }
            });

            if (!response.ok) throw new Error('Failed to delete business');

            WebApp.showAlert('Business deleted successfully. 👋');
            window.location.reload();
        } catch (err) {
            console.error(err);
            WebApp.showAlert('Error: ' + err.message);
            loader.classList.add('hidden');
            editView.classList.remove('hidden');
        }
    });
}

// Event Listeners

bizSelect.addEventListener('change', (e) => {
    updateBusinessView(e.target.value);
});

closeBtn.addEventListener('click', () => {
    WebApp.close();
});

editBtn.addEventListener('click', showEditPage);
editBackBtn.addEventListener('click', hideEditPage);
editForm.addEventListener('submit', saveBusinessChanges);
deleteBtn.addEventListener('click', deleteBusiness);

// Photo manager events
document.getElementById('photo-manager').addEventListener('click', (e) => {
    const btn = e.target.closest('.remove-photo');
    if (!btn) return;

    const type = btn.dataset.type;
    const index = parseInt(btn.dataset.index);

    if (type === 'existing') {
        const removed = photoState.existing.splice(index, 1)[0];
        photoState.removed.push(removed.slot);
    } else if (type === 'new') {
        photoState.newFiles.splice(index, 1);
    }
    renderPhotoManager();
});

document.getElementById('add-photo-btn').addEventListener('click', () => {
    const totalPhotos = photoState.existing.length + photoState.newFiles.length;
    if (totalPhotos >= 3) {
        WebApp.showAlert('Maximum 3 photos allowed.');
        return;
    }
    const input = document.getElementById('photo-upload');
    input.value = '';
    input.click();
});

document.getElementById('photo-upload').addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    const totalPhotos = photoState.existing.length + photoState.newFiles.length;
    const slotsLeft = 3 - totalPhotos;

    files.slice(0, slotsLeft).forEach(file => {
        photoState.newFiles.push(file);
    });
    renderPhotoManager();
});

// Start
// Start
init();

function triggerRegistration() {
    WebApp.sendData("trigger_register_from_dashboard");
    WebApp.close();
}

