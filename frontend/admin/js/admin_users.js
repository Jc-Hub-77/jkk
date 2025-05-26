// frontend/admin/js/admin_users.js
console.log("admin_users.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    // const isAdmin = localStorage.getItem('isAdmin') === 'true';
    // if (!isAdmin || !authToken) { /* Redirect */ }

    const usersTableBody = document.getElementById('usersTableBody');
    // TODO: Add elements for search input and pagination controls in admin_users.html

    async function fetchUsers(page = 1, searchTerm = '') {
        if (!usersTableBody) return;
        usersTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">Loading users...</td></tr>`;

        try {
            // Conceptual API: GET /api/admin/users?page=${page}&search=${searchTerm}
            const response = await fetch(`/api/v1/admin/users?page=${page}&search_term=${encodeURIComponent(searchTerm)}`, { 
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status, users, total_users, page, per_page, total_pages }
            
            if (data.status === "success" && data.users) {
                usersTableBody.innerHTML = ''; 
                if (data.users.length === 0) {
                    usersTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No users found.</td></tr>';
                    return;
                }
                data.users.forEach(user => {
                    const row = usersTableBody.insertRow();
                    row.insertCell().textContent = user.id;
                    row.insertCell().textContent = user.username;
                    row.insertCell().textContent = user.email;
                    row.insertCell().innerHTML = user.is_admin ? '<span style="color:var(--success-color); font-weight:bold;">Yes</span>' : 'No';
                    row.insertCell().textContent = new Date(user.created_at).toLocaleDateString();
                    
                    const actionsCell = row.insertCell();
                    // Edit button (placeholder)
                    // const editButton = document.createElement('button'); ... actionsCell.appendChild(editButton);

                    const toggleAdminButton = document.createElement('button');
                    toggleAdminButton.className = 'btn btn-sm';
                    toggleAdminButton.textContent = user.is_admin ? 'Revoke Admin' : 'Make Admin';
                    toggleAdminButton.style.backgroundColor = user.is_admin ? 'var(--secondary-color)' : 'var(--primary-color)';
                    toggleAdminButton.style.marginRight = '5px';
                    toggleAdminButton.onclick = () => handleToggleAdmin(user.id, !user.is_admin);
                    actionsCell.appendChild(toggleAdminButton);

                    const toggleActiveButton = document.createElement('button');
                    toggleActiveButton.className = 'btn btn-sm';
                    toggleActiveButton.textContent = user.is_active ? 'Deactivate' : 'Activate';
                    toggleActiveButton.style.backgroundColor = user.is_active ? 'var(--danger-color)' : 'var(--success-color)';
                    toggleActiveButton.onclick = () => handleToggleActive(user.id, !user.is_active);
                    actionsCell.appendChild(toggleActiveButton);
                });
                // TODO: Render pagination controls using data.total_pages, data.page
            } else {
                throw new Error(data.message || "Failed to parse user list.");
            }
        } catch (error) {
            console.error("Error fetching users:", error);
            usersTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">Error loading users: ${error.message}</td></tr>`;
        }
    }

    async function handleToggleAdmin(userId, makeAdmin) {
        if (!confirm(`Are you sure you want to ${makeAdmin ? 'grant admin rights to' : 'revoke admin rights from'} user ID ${userId}?`)) return;
        
        try {
            // Conceptual API: POST /api/admin/users/{userId}/set-admin-status
            const response = await fetch(`/api/v1/admin/users/set-admin-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                body: JSON.stringify({ user_id: userId, make_admin: makeAdmin })
            });
            if (!response.ok) throw new Error( (await response.json().catch(()=>({})) ).message || `HTTP error! status: ${response.status}`);
            const result = await response.json();

            if (result.status === "success") {
                alert(result.message);
                fetchUsers(); // Refresh list
            } else { throw new Error(result.message || "Failed to update admin status."); }
        } catch (error) {
            console.error("Error toggling admin status:", error);
            alert("Error: " + error.message);
        }
    }

    async function handleToggleActive(userId, setActive) {
        if (!confirm(`Are you sure you want to ${setActive ? 'activate' : 'deactivate'} user ID ${userId}?`)) return;

        try {
            // Conceptual API: POST /api/admin/users/{userId}/set-active-status
            const response = await fetch(`/api/v1/admin/users/toggle-active-status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                body: JSON.stringify({ user_id: userId, make_admin: setActive }) // Backend expects make_admin for toggle-active-status
            });
            if (!response.ok) throw new Error( (await response.json().catch(()=>({})) ).message || `HTTP error! status: ${response.status}`);
            const result = await response.json();
            
            if (result.status === "success") {
                alert(result.message);
                fetchUsers(); // Refresh list
            } else { throw new Error(result.message || "Failed to update active status."); }
        } catch (error) {
            console.error("Error toggling active status:", error);
            alert("Error: " + error.message);
        }
    }
    
    fetchUsers();
});
