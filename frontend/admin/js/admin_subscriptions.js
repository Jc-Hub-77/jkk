// frontend/admin/js/admin_subscriptions.js
console.log("admin_subscriptions.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    // const isAdmin = localStorage.getItem('isAdmin') === 'true';
    // if (!isAdmin || !authToken) { /* Redirect */ }

    const subscriptionsTableBody = document.getElementById('subscriptionsTableBody');
    // TODO: Add elements for search/filter and pagination in admin_subscriptions.html

    async function fetchAdminSubscriptions(page = 1, searchTerm = '') {
        if (!subscriptionsTableBody) return;
        subscriptionsTableBody.innerHTML = '<tr><td colspan="8" style="text-align:center;">Loading subscriptions...</td></tr>';

        try {
            // Conceptual API: GET /api/admin/subscriptions?page=${page}&search=${searchTerm}
            // const response = await fetch(`/api/admin/subscriptions?page=${page}&search=${encodeURIComponent(searchTerm)}`, { 
            //     headers: { 'Authorization': `Bearer ${authToken}` } 
            // });
            // if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            // const data = await response.json(); // Expects { status, subscriptions, total_subscriptions, ... }
            
            await new Promise(resolve => setTimeout(resolve, 500));
            const now = new Date();
            const allSimulatedSubs = [
                { id: 1, user_id: 101, username: "testuser", strategy_id: "ema_crossover_v1", strategy_name: "EMA Crossover", api_key_id: 1, is_active: true, subscribed_at: new Date(now.getTime() - 10*24*60*60*1000).toISOString(), expires_at: new Date(now.getTime() + 20*24*60*60*1000).toISOString() },
                { id: 2, user_id: 102, username: "anotheruser", strategy_id: "rsi_divergence_v1", strategy_name: "RSI Divergence", api_key_id: 2, is_active: false, subscribed_at: new Date(now.getTime() - 40*24*60*60*1000).toISOString(), expires_at: new Date(now.getTime() - 10*24*60*60*1000).toISOString() },
                { id: 3, user_id: 101, username: "testuser", strategy_id: "rsi_divergence_v1", strategy_name: "RSI Divergence", api_key_id: 1, is_active: true, subscribed_at: new Date(now.getTime() - 5*24*60*60*1000).toISOString(), expires_at: new Date(now.getTime() + 25*24*60*60*1000).toISOString() }
            ];
            // Simple search simulation
            const filteredSubs = searchTerm ? allSimulatedSubs.filter(s => String(s.user_id).includes(searchTerm) || s.username.includes(searchTerm) || s.strategy_id.includes(searchTerm) || s.strategy_name.includes(searchTerm)) : allSimulatedSubs;
            const data = { status: "success", subscriptions: filteredSubs, total_subscriptions: filteredSubs.length, page:1, per_page:20, total_pages:1 };


            if (data.status === "success" && data.subscriptions) {
                subscriptionsTableBody.innerHTML = '';
                if (data.subscriptions.length === 0) {
                    subscriptionsTableBody.innerHTML = '<tr><td colspan="8" style="text-align:center;">No subscriptions found.</td></tr>';
                    return;
                }
                data.subscriptions.forEach(sub => {
                    const row = subscriptionsTableBody.insertRow();
                    row.insertCell().textContent = sub.id;
                    row.insertCell().textContent = `${sub.user_id} (${sub.username || 'N/A'})`; // Include username if available
                    row.insertCell().textContent = `${sub.strategy_id} (${sub.strategy_name || 'N/A'})`;
                    row.insertCell().textContent = sub.api_key_id;
                    
                    const isActive = sub.is_active && new Date(sub.expires_at) > new Date();
                    row.insertCell().innerHTML = isActive ? 
                        '<span style="color:var(--success-color);">Active</span>' : '<span style="color:var(--danger-color);">Inactive/Expired</span>';
                    
                    row.insertCell().textContent = new Date(sub.subscribed_at).toLocaleString();
                    row.insertCell().textContent = new Date(sub.expires_at).toLocaleString();
                    
                    const actionsCell = row.insertCell();
                    const viewButton = document.createElement('button');
                    viewButton.className = 'btn btn-sm btn-outline';
                    viewButton.textContent = 'Details';
                    viewButton.onclick = () => handleViewSubscription(sub.id, sub);
                    actionsCell.appendChild(viewButton);

                    const cancelButton = document.createElement('button');
                    cancelButton.className = 'btn btn-sm btn-danger';
                    cancelButton.textContent = 'Cancel Sub';
                    cancelButton.style.marginLeft = '5px';
                    cancelButton.disabled = !isActive; // Can only cancel active subs
                    cancelButton.onclick = () => handleCancelSubscription(sub.id);
                    actionsCell.appendChild(cancelButton);
                });
                // TODO: Render pagination
            } else {
                throw new Error(data.message || "Failed to parse subscriptions list.");
            }
        } catch (error) {
            console.error("Error fetching admin subscriptions:", error);
            subscriptionsTableBody.innerHTML = `<tr><td colspan="8" style="text-align:center;">Error loading subscriptions: ${error.message}</td></tr>`;
        }
    }

    function handleViewSubscription(subscriptionId, subData) {
        alert(`Simulating view details for subscription ID: ${subscriptionId}.\nUser: ${subData.user_id}, Strategy: ${subData.strategy_id}, Expires: ${new Date(subData.expires_at).toLocaleString()}`);
        // TODO: Open modal with full subscription details, custom params, associated payment, etc.
    }

    async function handleCancelSubscription(subscriptionId) {
        if (!confirm(`Are you sure you want to cancel subscription ID ${subscriptionId}? This may stop the live trading bot.`)) return;

        try {
            // Conceptual API: POST /api/admin/subscriptions/{subscriptionId}/cancel
            // const response = await fetch(`/api/admin/subscriptions/${subscriptionId}/cancel`, {
            //     method: 'POST',
            //     headers: { 'Authorization': `Bearer ${authToken}` }
            // });
            // if (!response.ok) throw new Error((await response.json().catch(()=>({}))).message || `HTTP error! status: ${response.status}`);
            // const result = await response.json();

            await new Promise(resolve => setTimeout(resolve, 300));
            const result = {status: "success", message: `Subscription ${subscriptionId} cancelled successfully.`};
            
            if (result.status === "success") {
                alert(result.message);
                fetchAdminSubscriptions(); // Refresh list
            } else { throw new Error(result.message || "Failed to cancel subscription."); }
        } catch (error) {
            console.error(`Error cancelling subscription ${subscriptionId}:`, error);
            alert("Error: " + error.message);
        }
    }
    
    fetchAdminSubscriptions();
});
