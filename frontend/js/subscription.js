// frontend/js/subscription.js
console.log("subscription.js loaded");

const BACKEND_API_BASE_URL = 'http://127.0.0.1:8000';

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    const userId = localStorage.getItem('userId');

    // if (!authToken || !userId) { /* Redirect */ }

    const activeStrategySubsList = document.getElementById('activeStrategySubscriptionsList');
    const platformPlanName = document.getElementById('platformPlanName');
    const platformPlanStatus = document.getElementById('platformPlanStatus');
    const platformPlanExpiry = document.getElementById('platformPlanExpiry');
    const renewPlatformSubBtn = document.getElementById('renewPlatformSubBtn');
    const paymentSection = document.getElementById('paymentSection'); // This section might be re-purposed or removed for redirect flow
    const paymentHistoryTableBody = document.getElementById('paymentHistoryTableBody');

    // Elements from the paymentSection that might be less used with Coinbase redirect flow
    // const paymentSectionTitle = document.getElementById('paymentSectionTitle');
    // const paymentItemName = document.getElementById('paymentItemName');
    // const paymentAmountCrypto = document.getElementById('paymentAmountCrypto');
    // const paymentCurrency = document.getElementById('paymentCurrency');
    // const paymentAddress = document.getElementById('paymentAddress');
    // const paymentExpiryTime = document.getElementById('paymentExpiryTime');
    // const paymentGatewayChargeId = document.getElementById('paymentGatewayChargeId');
    // const paymentMadeBtn = document.getElementById('paymentMadeBtn');
    // const cancelPaymentBtn = document.getElementById('cancelPaymentBtn');


    async function initializeSubscriptionPage() {
        await fetchActiveStrategySubscriptions();
        await fetchPlatformSubscriptionDetails(); // Assuming a general platform sub exists
        await fetchPaymentHistory();

        if(renewPlatformSubBtn) renewPlatformSubBtn.addEventListener('click', handleRenewPlatformSubscription);
        // Event listeners for strategy renewal buttons are added dynamically in fetchActiveStrategySubscriptions
        
        // Hide the direct payment details section initially, as Coinbase uses redirects
        if(paymentSection) paymentSection.style.display = 'none'; 
    }

    async function fetchActiveStrategySubscriptions() {
        if (!activeStrategySubsList) return;
        activeStrategySubsList.innerHTML = '<p>Loading...</p>';
        try {
            // Conceptual API: GET /api/users/{userId}/strategy_subscriptions
            // const response = await fetch(`${BACKEND_API_BASE_URL}/api/users/${userId}/strategy_subscriptions`, { headers: { 'Authorization': `Bearer ${authToken}` } });
            // if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            // const data = await response.json(); // Expects { status: "success", subscriptions: [...] }
            
            await new Promise(resolve => setTimeout(resolve, 300));
            const data = { // Matches backend list_user_subscriptions format
                status: "success",
                subscriptions: [
                    { subscription_id: "sub_ema123", strategy_name: "EMA Crossover (BTC/USDT)", api_key_id: "key_sim_1", is_active: true, expires_at: new Date(Date.now() + 15*24*60*60*1000).toISOString(), time_remaining_seconds: 15*24*60*60, custom_parameters: {short_ema:10} },
                    { subscription_id: "sub_rsi456", strategy_name: "RSI Divergence (ETH/USDT)", api_key_id: "key_sim_2", is_active: false, expires_at: new Date(Date.now() - 5*24*60*60*1000).toISOString(), time_remaining_seconds: 0, custom_parameters: {rsi_period:14} }
                ]
            };

            activeStrategySubsList.innerHTML = '';
            if (!data.subscriptions || data.subscriptions.length === 0) {
                activeStrategySubsList.innerHTML = '<p>No active strategy subscriptions found.</p>'; return;
            }
            data.subscriptions.forEach(sub => {
                const itemDiv = document.createElement('div');
                itemDiv.className = 'subscription-item card mb-2'; // Added card styling
                const isActive = sub.is_active; // Backend now calculates this based on expiry
                const statusClass = isActive ? 'status-active' : 'status-expired';
                const timeRemaining = isActive ? formatTimeRemaining(sub.time_remaining_seconds) : 'Expired';

                itemDiv.innerHTML = `
                    <h4>${sub.strategy_name}</h4>
                    <div class="subscription-details">
                        <p><strong>Status:</strong> <span class="${statusClass}">${isActive ? 'Active' : 'Expired'}</span></p>
                        <p><strong>Expires:</strong> ${new Date(sub.expires_at).toLocaleString()} (${timeRemaining})</p>
                        <p><small>Subscription ID: ${sub.subscription_id} | API Key ID: ${sub.api_key_id}</small></p>
                    </div>
                    <button class="btn btn-sm renew-strategy-sub-btn mt-1" 
                            data-sub-id="${sub.subscription_id}" 
                            data-item-name="${sub.strategy_name}"
                            data-item-type="renew_strategy_subscription"
                            data-item-description="1 Month Renewal for ${sub.strategy_name}"
                            data-amount-usd="10.00"> <!-- Assuming fixed renewal price -->
                        Renew ($10.00)
                    </button>
                `;
                activeStrategySubsList.appendChild(itemDiv);
            });
            document.querySelectorAll('.renew-strategy-sub-btn').forEach(button => {
                button.addEventListener('click', handleGenericRenewInitiation);
            });
        } catch (error) {
            console.error("Error loading strategy subscriptions:", error);
            activeStrategySubsList.innerHTML = `<p class="error-message">Error: ${error.message}</p>`;
        }
    }

    async function fetchPlatformSubscriptionDetails() {
        // Conceptual API: GET /api/users/{userId}/platform_subscription
        try {
            // const response = await fetch(`${BACKEND_API_BASE_URL}/api/users/${userId}/platform_subscription`, { headers: { /* Auth */ } });
            // const data = await response.json();
            await new Promise(resolve => setTimeout(resolve, 200));
            const data = {
                status: "success",
                subscription: { plan_name: "Premium Annual", is_active: true, expires_at: new Date(Date.now() + 90*24*60*60*1000).toISOString() }
            };

            if (data.status === "success" && data.subscription) {
                const sub = data.subscription;
                if(platformPlanName) platformPlanName.textContent = sub.plan_name;
                const isActive = sub.is_active && new Date(sub.expires_at) > new Date();
                if(platformPlanStatus) {
                    platformPlanStatus.textContent = isActive ? 'Active' : 'Expired';
                    platformPlanStatus.className = isActive ? 'status-active' : 'status-expired';
                }
                if(platformPlanExpiry) platformPlanExpiry.textContent = new Date(sub.expires_at).toLocaleDateString();
                if(renewPlatformSubBtn) {
                    renewPlatformSubBtn.style.display = 'inline-block';
                    renewPlatformSubBtn.dataset.itemId = "platform_annual"; // Unique ID for this item
                    renewPlatformSubBtn.dataset.itemName = "Platform Access (Annual)";
                    renewPlatformSubBtn.dataset.itemType = "platform_subscription";
                    renewPlatformSubBtn.dataset.itemDescription = "1 Year Platform Access Renewal";
                    renewPlatformSubBtn.dataset.amountUsd = "99.00"; // Example price
                }
            } else { throw new Error(data.message || "Failed to load platform subscription."); }
        } catch (error) {
            console.error("Error loading platform subscription:", error);
            if(platformPlanName) platformPlanName.textContent = "N/A";
        }
    }

    async function fetchPaymentHistory() {
        if (!paymentHistoryTableBody) return;
        paymentHistoryTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Loading...</td></tr>';
        try {
            // Conceptual API: GET /api/users/{userId}/payments
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/payment/history/me`, { headers: { 'Authorization': `Bearer ${authToken}` } });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status:"success", payment_history: [] }

            paymentHistoryTableBody.innerHTML = '';
            if (!data.payment_history || data.payment_history.length === 0) {
                paymentHistoryTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No payment history.</td></tr>'; return;
            }
            data.payment_history.forEach(p => {
                const row = paymentHistoryTableBody.insertRow();
                row.insertCell().textContent = new Date(p.date).toLocaleDateString();
                row.insertCell().textContent = p.description;
                row.insertCell().textContent = p.amount.toFixed(p.currency === "USDC" ? 2 : 8);
                row.insertCell().textContent = p.currency;
                row.insertCell().innerHTML = `<span class="status-${p.status}">${p.status}</span>`;
                row.insertCell().textContent = p.gateway_id;
            });
        } catch (error) {
            console.error("Error loading payment history:", error);
            paymentHistoryTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">Error: ${error.message}</td></tr>`;
        }
    }

    async function handleGenericRenewInitiation(event) {
        const button = event.target;
        const itemId = button.dataset.subId || button.dataset.itemId; // subId for strategy, itemId for platform
        const itemName = button.dataset.itemName;
        const itemType = button.dataset.itemType;
        const itemDescription = button.dataset.itemDescription || itemName; // Default description to name
        const amountUsd = parseFloat(button.dataset.amountUsd);
        const subscriptionMonths = parseInt(button.dataset.subscriptionMonths || "1");

        console.log(`Initiating payment for ${itemType} ID ${itemId}: ${itemName}, Amount: $${amountUsd}`);
        
        // Show a loading/processing state on the button
        button.disabled = true;
        button.textContent = "Processing...";

        try {
            // Conceptual API: POST /api/payments/create_charge 
            // (calls backend's create_coinbase_commerce_charge)
            const response = await fetch(`${BACKEND_API_BASE_URL}/api/v1/payment/charges`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                body: JSON.stringify({ 
                    item_id: itemId, // Use itemId from dataset
                    item_type: itemType, // Use itemType from dataset
                    item_name: itemName, // Use itemName from dataset
                    item_description: itemDescription, // Use itemDescription from dataset
                    amount_usd: amountUsd, // Use amountUsd from dataset
                    subscription_months: subscriptionMonths, // Use subscriptionMonths from dataset
                    // Include redirect and cancel URLs if needed by the backend
                    redirect_url: window.location.href, // Redirect back to this page on success
                    cancel_url: window.location.href // Redirect back to this page on cancel
                    // Add metadata if required by backend for specific item types (e.g., api_key_id for new subscriptions)
                    // metadata: { api_key_id: '...', custom_parameters_json: '...' } 
                })
            });
            const chargeData = await response.json(); // Expects { status, payment_page_url, ... }
            if (!response.ok || chargeData.status === "error") {
                 throw new Error(chargeData.message || `HTTP error! status: ${response.status}`);
            }
            
            if (chargeData.status.startsWith("success") && chargeData.payment_page_url) {
                alert(`You will now be redirected to Coinbase Commerce to complete your payment for ${itemName}.`);
                window.location.href = chargeData.payment_page_url; // Redirect to Coinbase
            } else {
                throw new Error(chargeData.message || "Failed to create payment charge.");
            }
        } catch (error) {
            console.error("Error initiating payment:", error);
            alert("Error initiating payment: " + error.message);
            button.disabled = false; // Re-enable button on error
            button.textContent = `Renew ($${amountUsd.toFixed(2)})`; // Reset text
        }
    }
    
    function handleRenewPlatformSubscription(event) { // Wrapper to call generic handler
        handleGenericRenewInitiation(event);
    }
    
    function formatTimeRemaining(totalSeconds) {
        if (totalSeconds <= 0) return "Expired";
        const days = Math.floor(totalSeconds / (24 * 60 * 60));
        const hours = Math.floor((totalSeconds % (24 * 60 * 60)) / (60 * 60));
        const minutes = Math.floor((totalSeconds % (60 * 60)) / 60);
        let parts = [];
        if (days > 0) parts.push(`${days}d`);
        if (hours > 0 && days < 3) parts.push(`${hours}h`); // Show hours if less than 3 days
        if (minutes > 0 && days === 0 && hours === 0) parts.push(`${minutes}m`);
        if (parts.length === 0 && totalSeconds > 0) return "<1m";
        return parts.join(' ') || "Expired";
    }

    initializeSubscriptionPage();
});
