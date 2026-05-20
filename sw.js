// Service Worker for Beach Status Notifications
const CACHE_NAME = 'beach-status-v7'; // Increment version to force reload
const beachName = 'Shannon Beach @ Upper Mystic (DCR)';

// Data is now served as static JSON from the same origin (built by a scheduled
// GitHub Action). Same-origin, no proxy, no CORS.
const statusURL = 'data/status.json';
const csoURL = 'data/cso.json';

async function fetchCSOData() {
    try {
        const response = await fetch(csoURL, { cache: 'no-store' });
        if (!response.ok) {
            throw new Error('CSO data response was not ok ' + response.statusText);
        }
        const csoData = await response.json();
        console.log('Service Worker: CSO data received, incidents count:', csoData.results?.length || 0);
        return csoData;
    } catch (error) {
        console.error('Service Worker: Error fetching CSO data:', error);
        return { results: [], rowCount: 0 };
    }
}

// Install event
self.addEventListener('install', event => {
    console.log('Service Worker installing');
    self.skipWaiting();
});

// Activate event
self.addEventListener('activate', event => {
    console.log('Service Worker activating');
    event.waitUntil(self.clients.claim());
});

// Background sync for periodic status checks
self.addEventListener('sync', event => {
    if (event.tag === 'beach-status-check') {
        event.waitUntil(checkBeachStatus());
    }
});

// Periodic background sync (Chrome only)
self.addEventListener('periodicsync', event => {
    if (event.tag === 'beach-status-periodic') {
        event.waitUntil(checkBeachStatus());
    }
});

// Get configuration settings from IndexedDB
async function getConfigSettings() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('BeachStatusDB', 1);

        request.onerror = () => resolve({
            isTestMode: false,
            testStatusData: null,
            syncFrequencyMinutes: 5
        });

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('status')) {
                db.createObjectStore('status', { keyPath: 'id' });
            }
        };

        request.onsuccess = (event) => {
            const db = event.target.result;
            const transaction = db.transaction(['status'], 'readonly');
            const store = transaction.objectStore('status');
            const getRequest = store.get('config');

            getRequest.onsuccess = () => {
                db.close();
                if (getRequest.result) {
                    resolve({
                        isTestMode: getRequest.result.isTestMode,
                        testStatusData: getRequest.result.testStatusData,
                        syncFrequencyMinutes: getRequest.result.syncFrequencyMinutes || 5
                    });
                } else {
                    resolve({
                        isTestMode: false,
                        testStatusData: null,
                        syncFrequencyMinutes: 5
                    });
                }
            };

            getRequest.onerror = () => {
                db.close();
                resolve({
                    isTestMode: false,
                    testStatusData: null,
                    syncFrequencyMinutes: 5
                });
            };
        };
    });
}

// Check beach status and send notifications
async function checkBeachStatus() {
    try {
        console.log('Service Worker: Checking beach status...');

        // Get configuration settings
        const config = await getConfigSettings();
        console.log('Service Worker: Config settings:', config);

        let statusObj = null;
        let csoData = { results: [], rowCount: 0 };

        if (config.statusOverride) {
            console.log('Service Worker: Using status override:', config.statusOverride);
            statusObj = {
                name: beachName,
                status: config.statusOverride.charAt(0).toUpperCase() + config.statusOverride.slice(1),
                town: 'Winchester',
            };
        } else if (config.isTestMode && config.testStatusData) {
            console.log('Service Worker: Using test data from IndexedDB');
            statusObj = parseStatusData(config.testStatusData);
            try {
                csoData = await fetchCSOData();
            } catch (error) {
                console.log('Service Worker: Error fetching CSO test data:', error);
            }
        } else {
            console.log('Service Worker: Fetching static data');
            const [statusResponse, csoDataResult] = await Promise.all([
                fetch(statusURL, { cache: 'no-store' }),
                fetchCSOData()
            ]);
            if (!statusResponse.ok) {
                throw new Error('Status response was not ok: ' + statusResponse.statusText);
            }
            statusObj = await statusResponse.json();
            csoData = csoDataResult;
        }

        const currentStatus = statusObj && statusObj.status
            ? statusObj.status.toLowerCase()
            : null;

        // Check CSO incidents affecting Mystic Lake
        const mysticCSOIncidents = csoData.results?.filter(incident => {
            const waterBody = incident.waterBodyDescription?.toLowerCase() || '';
            return waterBody.includes('mystic') || waterBody.includes('upper mystic');
        }) || [];

        console.log('Service Worker: CSO incidents affecting Mystic Lake:', mysticCSOIncidents.length);

        // Get previous status from storage
        const previousStatus = await getStoredStatus();

        console.log('Service Worker: Status check - Previous:', previousStatus, 'Current:', currentStatus);

        // Always store current status (even on first run)
        if (currentStatus) {
            await storeStatus(currentStatus);
            console.log('Service Worker: Status stored:', currentStatus);
        }

        // Send notification only if status changed and we have notification permission
        if (previousStatus && previousStatus !== currentStatus && currentStatus) {
            console.log('Service Worker: Status changed, attempting notification...');
            await showStatusNotification(currentStatus, previousStatus);
        } else if (!previousStatus) {
            console.log('Service Worker: First run, status stored but no notification sent');
        }

    } catch (error) {
        console.error('Service Worker: Error checking beach status:', error);
    }
}

// Parse a legacy CSV test fixture into the {name, status, town} object used by the rest
// of the worker. Used only when testStatusData (a CSV string) is supplied via IndexedDB.
function parseStatusData(csvText) {
    try {
        const lines = csvText.trim().split('\n');
        if (lines.length < 2) return null;
        const cells = lines[1].split(',').map(c => c.trim());
        return { name: cells[0], status: cells[1], town: cells[2] };
    } catch (error) {
        console.error('Service Worker: Error parsing status CSV:', error);
        return null;
    }
}

// Use simple emoji icon as fallback - browsers handle this better
function createNotificationIcon(isOpen) {
    const emoji = isOpen ? '🏊‍♂️' : '🚫';
    // Simple SVG that most browsers can handle
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100"><text y="80" x="50" font-size="80" text-anchor="middle">${emoji}</text></svg>`;
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// Show desktop notification for status changes
async function showStatusNotification(newStatus, previousStatus) {
    const isOpen = newStatus === 'open';
    const title = 'Shannon Beach Status Update';
    const statusText = isOpen ? 'Open for Swimming' : 'Closed for Swimming';
    const body = `Swimming status changed to: ${statusText}`;

    // Create custom icon and badge
    const icon = createNotificationIcon(isOpen);
    const badge = createNotificationIcon(false); // Always use closed icon for badge

    // Simplified notification options that actually work
    const notificationOptions = {
        body: body,
        icon: icon,
        requireInteraction: true,  // This is the main persistence flag that works
        tag: 'beach-status',       // Keep same tag to replace old notifications
        renotify: true,           // Force show even with same tag
        silent: false,            // Allow sound
        data: {
            status: newStatus,
            previousStatus: previousStatus,
            timestamp: Date.now(),
            url: '/'
        }
    };

    try {
        await self.registration.showNotification(title, notificationOptions);
        console.log('Service Worker: Persistent notification sent for status change:', previousStatus, '->', newStatus);

        // Simple debug logging
        console.log('Service Worker: Notification shown with icon:', icon.substring(0, 50) + '...');

    } catch (error) {
        console.error('Service Worker: Error showing notification:', error);
    }
}

// Store status in IndexedDB
async function storeStatus(status) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('BeachStatusDB', 1);

        request.onerror = () => reject(request.error);

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('status')) {
                db.createObjectStore('status', { keyPath: 'id' });
            }
        };

        request.onsuccess = (event) => {
            const db = event.target.result;
            const transaction = db.transaction(['status'], 'readwrite');
            const store = transaction.objectStore('status');

            store.put({
                id: 'current',
                status: status,
                timestamp: Date.now()
            });

            transaction.oncomplete = () => {
                db.close();
                resolve();
            };

            transaction.onerror = () => {
                db.close();
                reject(transaction.error);
            };
        };
    });
}

// Get stored status from IndexedDB
async function getStoredStatus() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('BeachStatusDB', 1);

        request.onerror = () => resolve(null); // Return null if DB doesn't exist yet

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('status')) {
                db.createObjectStore('status', { keyPath: 'id' });
            }
        };

        request.onsuccess = (event) => {
            const db = event.target.result;
            const transaction = db.transaction(['status'], 'readonly');
            const store = transaction.objectStore('status');
            const getRequest = store.get('current');

            getRequest.onsuccess = () => {
                db.close();
                resolve(getRequest.result ? getRequest.result.status : null);
            };

            getRequest.onerror = () => {
                db.close();
                resolve(null);
            };
        };
    });
}

// Handle notification clicks and action buttons
self.addEventListener('notificationclick', event => {
    console.log('Service Worker: Notification clicked:', event.action);

    // Handle action button clicks
    if (event.action === 'dismiss') {
        event.notification.close();
        return;
    }

    if (event.action === 'view' || !event.action) {
        event.notification.close();

        // Focus or open the beach status page
        event.waitUntil(
            self.clients.matchAll({ type: 'window' }).then(clients => {
                // Try to focus existing client
                for (const client of clients) {
                    if ('focus' in client) {
                        return client.focus();
                    }
                }
                // If no existing client, open new one
                if (self.clients.openWindow) {
                    return self.clients.openWindow('/');
                }
            })
        );
    }
});

// Handle notification close events
self.addEventListener('notificationclose', event => {
    console.log('Service Worker: Notification closed by user');
});

// Message handling from main thread
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'CHECK_STATUS') {
        checkBeachStatus();
    }
});
