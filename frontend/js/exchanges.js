// frontend/js/exchanges.js
console.log("exchanges.js loaded");

const BACKEND_API_BASE_URL = 'http://127.0.0.1:8000';

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken'); // Assumed to be set on login
    const userId = localStorage.getItem('userId'); // Assumed to be set on login

    // Basic auth check for page access
    // if (!authToken || !userId) {
    //     console.warn("User not authenticated. Redirecting to login.");
    //     window.location.href = 'login.html'; // Adjust path if auth.js is in a different relative location
    //     return;
    // }

    const addExchangeForm = document.getElementById('addExchangeForm');
    const exchangeNameSelect = document.getElementById('exchangeName');
    const connectedExchangesTableBody = document.getElementById('connectedExchangesTableBody');

    // --- Initialization ---
    async function initializeExchangesPage() {
        await populateExchangeDropdown();
        await fetchAndDisplayConnectedExchanges();
    }

    async function populateExchangeDropdown() {
        if (!exchangeNameSelect) return;
        exchangeNameSelect.innerHTML = '<option value="">Loading exchanges...</option>';
        
        try {
            // Conceptual API call to get supported exchanges
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/exchanges/supported`, {
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const supportedExchanges = await response.json(); // Assuming backend returns a list of strings

            // Filter supported exchanges based on the user's specified list
            const allowedExchanges = ["binance", "bybit", "phemex", "binanceus", "bitget", "coinbase"];
            const filteredExchanges = supportedExchanges.filter(exchangeId => allowedExchanges.includes(exchangeId.toLowerCase()));

            exchangeNameSelect.innerHTML = '<option value="">Select an Exchange</option>';
            filteredExchanges.sort().forEach(exchangeId => {
                const option = document.createElement('option');
                option.value = exchangeId.toLowerCase();
                // Capitalize first letter for display, handle multi-word names if any (e.g. coinbasepro -> Coinbase Pro)
                option.textContent = exchangeId.charAt(0).toUpperCase() + exchangeId.slice(1).replace(/([A-Z])/g, ' $1').trim();
                exchangeNameSelect.appendChild(option);
            });
        } catch (error) {
            console.error("Failed to load supported exchanges:", error);
            exchangeNameSelect.innerHTML = '<option value="">Error loading exchanges</option>';
        }
    }

    async function fetchAndDisplayConnectedExchanges() {
        if (!connectedExchangesTableBody) return;
        connectedExchangesTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Fetching your connected exchanges...</td></tr>';

        try {
            // Conceptual API call to backend to get user's API keys
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/users/${userId}/exchange_keys`, { // RESTful endpoint
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status: "success", keys: [...] }
            
            if (data.status === "success" && data.keys) {
                connectedExchangesTableBody.innerHTML = ''; 
                if (data.keys.length === 0) {
                    connectedExchangesTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No API keys added yet.</td></tr>';
                    return;
                }
                data.keys.forEach(key => {
                    const row = connectedExchangesTableBody.insertRow();
                    row.insertCell().textContent = key.label;
                    row.insertCell().textContent = key.exchange_name;
                    row.insertCell().textContent = key.api_key_preview;
                    
                    const statusCell = row.insertCell();
                    statusCell.textContent = key.status.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()); // Format status
                    statusCell.className = `status-${key.status.replace(/_/g, '-')}`;

                    const actionsCell = row.insertCell();
                    const testButton = document.createElement('button');
                    testButton.textContent = "Test";
                    testButton.className = "btn btn-sm btn-outline test-key-btn"; // Use global .btn classes
                    testButton.dataset.keyId = key.id;
                    testButton.disabled = key.status === "error_decryption" || key.status === "testing";

                    const removeButton = document.createElement('button');
                    removeButton.textContent = "Remove";
                    removeButton.className = "btn btn-sm btn-danger remove-key-btn"; // Use global .btn classes
                    removeButton.dataset.keyId = key.id;
                    removeButton.style.marginLeft = "5px";

                    actionsCell.appendChild(testButton);
                    actionsCell.appendChild(removeButton);
                });
            } else {
                throw new Error(data.message || "Failed to parse exchange keys.");
            }
        } catch (error) {
            console.error("Failed to fetch connected exchanges:", error);
            connectedExchangesTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center;">Error loading exchanges: ${error.message}</td></tr>`;
        }
    }

    if (addExchangeForm) {
        addExchangeForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const formData = new FormData(addExchangeForm);
            const exchangeData = {
                // user_id: userId, // Backend should get this from authenticated session/token
                exchange_name: formData.get('exchangeName'),
                label: formData.get('apiKeyLabel'),
                api_key_public: formData.get('apiKey'), // Matching backend param name
                secret_key: formData.get('secretKey'),
                passphrase: formData.get('passphrase') || null
            };

            console.log("Submitting new API key:", exchangeData);
            try {
                // Conceptual API call to backend: POST /api/users/{userId}/exchange_keys
                const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/users/${userId}/exchange_keys`, {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${authToken}` 
                    },
                    body: JSON.stringify(exchangeData)
                });
                const result = await response.json();
                if (!response.ok || result.status === "error") {
                    throw new Error(result.message || `HTTP error! status: ${response.status}`);
                }

                if (result.status === "success") {
                    alert(result.message);
                    addExchangeForm.reset();
                    fetchAndDisplayConnectedExchanges(); // Refresh the list
                    if (result.api_key_id) { // Optionally, automatically trigger a test
                        handleTestKey(result.api_key_id, "Test after adding");
                    }
                } else {
                    alert("Failed to add API key: " + result.message);
                }
            } catch (error) {
                console.error("Error adding API key:", error);
                alert("Error adding API key: " + error.message);
            }
        });
    }

    async function handleTestKey(apiKeyId, buttonTextContext = "Test") {
        const testButton = document.querySelector(`.test-key-btn[data-key-id="${apiKeyId}"]`);
        if(testButton) testButton.disabled = true; testButton.textContent = "Testing...";

        console.log(`Testing API Key ID: ${apiKeyId}`);
        try {
            // Conceptual API call: POST /api/users/{userId}/exchange_keys/{apiKeyId}/test
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/users/${userId}/exchange_keys/${apiKeyId}/test`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            const result = await response.json();
            if (!response.ok || result.status === "error") {
                 throw new Error(result.message || `HTTP error! status: ${response.status}`);
            }
            
            alert(`Test result for key ${apiKeyId}: ${result.message} (Status: ${result.status})`);
        } catch (error) {
            console.error(`Error testing API key ${apiKeyId}:`, error);
            alert(`Error testing API key: ${error.message}`);
        } finally {
            if(testButton) { testButton.disabled = false; testButton.textContent = buttonTextContext; }
            fetchAndDisplayConnectedExchanges(); // Refresh list to show updated status
        }
    }

    async function handleRemoveKey(apiKeyId) {
        if (!confirm(`Are you sure you want to remove API Key ID: ${apiKeyId}? This action cannot be undone.`)) {
            return;
        }
        console.log(`Removing API Key ID: ${apiKeyId}`);
        try {
            // Conceptual API call: DELETE /api/users/{userId}/exchange_keys/{apiKeyId}
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/users/${userId}/exchange_keys/${apiKeyId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            const result = await response.json();
            if (!response.ok || result.status === "error") {
                 throw new Error(result.message || `HTTP error! status: ${response.status}`);
            }
            
            if (result.status === "success") {
                alert(result.message);
            } else {
                alert("Failed to remove API key: " + result.message);
            }
        } catch (error) {
            console.error(`Error removing API key ${apiKeyId}:`, error);
            alert(`Error removing API key: ${error.message}`);
        } finally {
            fetchAndDisplayConnectedExchanges(); // Refresh list
        }
    }

    // Event delegation for test and remove buttons
    if (connectedExchangesTableBody) {
        connectedExchangesTableBody.addEventListener('click', (event) => {
            const targetButton = event.target.closest('button');
            if (!targetButton) return;

            const keyId = targetButton.dataset.keyId;
            if (targetButton.classList.contains('test-key-btn')) {
                handleTestKey(keyId);
            } else if (targetButton.classList.contains('remove-key-btn')) {
                handleRemoveKey(keyId);
            }
        });
    }
    
    initializeExchangesPage();
});
