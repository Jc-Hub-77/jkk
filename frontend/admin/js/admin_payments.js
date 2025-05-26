// frontend/admin/js/admin_payments.js
console.log("admin_payments.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    // const isAdmin = localStorage.getItem('isAdmin') === 'true';
    // if (!isAdmin || !authToken) { /* Redirect */ }

    const paymentsTableBody = document.getElementById('paymentsTableBody');
    // TODO: Add search/filter and pagination controls in admin_payments.html

    async function fetchAdminPayments(page = 1, searchTerm = '') {
        if (!paymentsTableBody) return;
        paymentsTableBody.innerHTML = '<tr><td colspan="9" style="text-align:center;">Loading payments...</td></tr>';

        try {
            // Conceptual API: GET /api/admin/payments?page=${page}&search=${searchTerm}
            const response = await fetch(`/api/v1/admin/transactions?page=${page}&search_term=${encodeURIComponent(searchTerm)}`, { 
                headers: { 'Authorization': `Bearer ${authToken}` } 
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status, payments, total_payments, ... }
            
            if (data.status === "success" && data.payments) {
                paymentsTableBody.innerHTML = '';
                if (data.payments.length === 0) {
                    paymentsTableBody.innerHTML = '<tr><td colspan="9" style="text-align:center;">No payment transactions found.</td></tr>';
                    return;
                }
                data.payments.forEach(p => {
                    const row = paymentsTableBody.insertRow();
                    row.insertCell().textContent = p.id; // Our internal Tx ID
                    row.insertCell().textContent = p.user_id;
                    row.insertCell().textContent = p.user_strategy_subscription_id || 'N/A';
                    row.insertCell().textContent = p.amount_crypto.toFixed(p.crypto_currency === "USDC" || p.crypto_currency === "USDT" ? 2 : 8);
                    row.insertCell().textContent = p.crypto_currency;
                    row.insertCell().textContent = p.payment_gateway_id;
                    row.insertCell().innerHTML = `<span class="status-${p.status}">${p.status}</span>`;
                    row.insertCell().textContent = new Date(p.created_at).toLocaleString();
                    
                    const actionsCell = row.insertCell();
                    const viewButton = document.createElement('button');
                    viewButton.className = 'btn btn-sm btn-outline';
                    viewButton.textContent = 'View';
                    viewButton.onclick = () => handleViewPayment(p.id, p);
                    actionsCell.appendChild(viewButton);
                    // Add manual update status button if needed (e.g., mark pending as completed/failed)
                });
                // TODO: Render pagination
            } else {
                throw new Error(data.message || "Failed to parse payments list.");
            }
        } catch (error) {
            console.error("Error fetching admin payments:", error);
            paymentsTableBody.innerHTML = `<tr><td colspan="9" style="text-align:center;">Error loading payments: ${error.message}</td></tr>`;
        }
    }

    function handleViewPayment(paymentId, paymentData) {
        alert(`Simulating view details for payment ID: ${paymentId}.\nUser: ${paymentData.user_id}, Amount: ${paymentData.amount_crypto} ${paymentData.crypto_currency}, Status: ${paymentData.status}`);
        // TODO: Open modal with full payment details, link to user, subscription.
    }
    
    fetchAdminPayments();
});
