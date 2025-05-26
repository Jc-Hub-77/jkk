// frontend/admin/js/admin_referrals.js
console.log("admin_referrals.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    // const isAdmin = localStorage.getItem('isAdmin') === 'true';
    // if (!isAdmin || !authToken) { /* Redirect */ }

    const referralsTableBody = document.getElementById('referralsTableBody');
    // TODO: Add elements for search/filter and pagination in admin_referrals.html

    async function fetchAdminReferrals(page = 1, searchTerm = '') {
        if (!referralsTableBody) return;
        referralsTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center;">Loading referral data...</td></tr>`;

        try {
            // Conceptual API: GET /api/admin/referrals?page=${page}&search=${searchTerm}
            // const response = await fetch(`/api/admin/referrals?page=${page}&search=${encodeURIComponent(searchTerm)}`, { 
            //     headers: { 'Authorization': `Bearer ${authToken}` } 
            // });
            // if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            // const data = await response.json(); // Expects { status, referrals, total, page, per_page, total_pages }
            
            await new Promise(resolve => setTimeout(resolve, 500));
            const now = new Date();
            const allSimulatedReferrals = [
                { referral_id: 1, referrer_user_id: 100, referrer_username: "referrerOne", referred_user_id: 101, referred_username: "testuser", signed_up_at: new Date(now.getTime() - 15*24*60*60*1000).toISOString(), first_payment_at: new Date(now.getTime() - 10*24*60*60*1000).toISOString(), commission_earned_total: 10.00, commission_pending_payout: 10.00, commission_paid_out_total: 0.00, last_payout_date: null },
                { referral_id: 2, referrer_user_id: 100, referrer_username: "referrerOne", referred_user_id: 102, referred_username: "anotheruser", signed_up_at: new Date(now.getTime() - 5*24*60*60*1000).toISOString(), first_payment_at: null, commission_earned_total: 0.00, commission_pending_payout: 0.00, commission_paid_out_total: 0.00, last_payout_date: null },
                { referral_id: 3, referrer_user_id: 102, referrer_username: "anotheruser", referred_user_id: 103, referred_username: "newbie", signed_up_at: new Date(now.getTime() - 1*24*60*60*1000).toISOString(), first_payment_at: new Date(now.getTime() - 0.5*24*60*60*1000).toISOString(), commission_earned_total: 5.00, commission_pending_payout: 0.00, commission_paid_out_total: 5.00, last_payout_date: new Date(now.getTime() - 0.1*24*60*60*1000).toISOString() }
            ];
            const filteredReferrals = searchTerm ? allSimulatedReferrals.filter(r => String(r.referrer_user_id).includes(searchTerm) || String(r.referred_user_id).includes(searchTerm) || r.referrer_username.includes(searchTerm) || r.referred_username.includes(searchTerm)) : allSimulatedReferrals;
            const data = { status: "success", referrals: filteredReferrals, total: filteredReferrals.length, page:1, per_page:20, total_pages:1 };

            if (data.status === "success" && data.referrals) {
                referralsTableBody.innerHTML = '';
                if (data.referrals.length === 0) {
                    referralsTableBody.innerHTML = '<tr><td colspan="10" style="text-align:center;">No referral records found.</td></tr>';
                    return;
                }
                data.referrals.forEach(ref => {
                    const row = referralsTableBody.insertRow();
                    row.insertCell().textContent = ref.referral_id;
                    row.insertCell().textContent = `${ref.referrer_user_id} (${ref.referrer_username})`;
                    row.insertCell().textContent = `${ref.referred_user_id} (${ref.referred_username})`;
                    row.insertCell().textContent = new Date(ref.signed_up_at).toLocaleDateString();
                    row.insertCell().textContent = ref.first_payment_at ? new Date(ref.first_payment_at).toLocaleDateString() : 'N/A';
                    row.insertCell().textContent = `$${ref.commission_earned_total.toFixed(2)}`;
                    row.insertCell().textContent = `$${ref.commission_pending_payout.toFixed(2)}`;
                    row.insertCell().textContent = `$${ref.commission_paid_out_total.toFixed(2)}`;
                    row.insertCell().textContent = ref.last_payout_date ? new Date(ref.last_payout_date).toLocaleDateString() : 'N/A';
                    
                    const actionsCell = row.insertCell();
                    if (ref.commission_pending_payout > 0) {
                        const payButton = document.createElement('button');
                        payButton.className = 'btn btn-sm btn-success';
                        payButton.textContent = 'Mark Paid';
                        payButton.onclick = () => handleMarkCommissionPaid(ref.referral_id, ref.commission_pending_payout);
                        actionsCell.appendChild(payButton);
                    } else {
                        actionsCell.textContent = 'N/A';
                    }
                });
                // TODO: Render pagination controls
            } else {
                throw new Error(data.message || "Failed to parse referral data.");
            }
        } catch (error) {
            console.error("Error fetching admin referrals:", error);
            referralsTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center;">Error loading referral data: ${error.message}</td></tr>`;
        }
    }

    async function handleMarkCommissionPaid(referralId, pendingAmount) {
        const amountToPay = prompt(`Enter amount paid for referral ID ${referralId} (pending: $${pendingAmount.toFixed(2)}):`, pendingAmount.toFixed(2));
        if (amountToPay === null) return; // User cancelled

        const parsedAmount = parseFloat(amountToPay);
        if (isNaN(parsedAmount) || parsedAmount <= 0 || parsedAmount > pendingAmount) {
            alert("Invalid amount entered. Please enter a positive number up to the pending amount.");
            return;
        }

        if (!confirm(`Mark $${parsedAmount.toFixed(2)} as paid for referral ID ${referralId}?`)) return;

        try {
            // Conceptual API: POST /api/admin/referrals/{referralId}/mark-paid
            // const response = await fetch(`/api/admin/referrals/${referralId}/mark-paid`, {
            //     method: 'POST',
            //     headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
            //     body: JSON.stringify({ amount_paid: parsedAmount, notes: "Paid via admin panel" })
            // });
            // if (!response.ok) throw new Error((await response.json().catch(()=>({}))).message || `HTTP error! status: ${response.status}`);
            // const result = await response.json();
            
            await new Promise(resolve => setTimeout(resolve, 300));
            const result = {status: "success", message: "Commission payout recorded."};

            if (result.status === "success") {
                alert(result.message);
                fetchAdminReferrals(); // Refresh list
            } else { throw new Error(result.message || "Failed to mark commission as paid."); }
        } catch (error) {
            console.error(`Error marking commission paid for referral ${referralId}:`, error);
            alert("Error: " + error.message);
        }
    }
    
    fetchAdminReferrals();
});
