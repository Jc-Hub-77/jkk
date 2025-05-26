// frontend/admin/js/admin_strategies.js
console.log("admin_strategies.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    const authToken = localStorage.getItem('authToken');
    // const isAdmin = localStorage.getItem('isAdmin') === 'true';
    // if (!isAdmin || !authToken) { /* Redirect */ }

    const strategiesTableBody = document.getElementById('strategiesTableBody');
    const addNewStrategyBtn = document.querySelector('header.page-header button'); // Assuming one button in header

    async function fetchAdminStrategies() {
        if (!strategiesTableBody) return;
        strategiesTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Loading strategies...</td></tr>';

        try {
            // Conceptual API: GET /api/admin/strategies
            const response = await fetch('/api/v1/admin/strategies', { headers: { 'Authorization': `Bearer ${authToken}` } });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json(); // Expects { status, strategies: [...] } (from PREDEFINED_STRATEGIES_METADATA for now)
            
            if (data.status === "success" && data.strategies) {
                strategiesTableBody.innerHTML = '';
                if (data.strategies.length === 0) {
                    strategiesTableBody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No strategies defined.</td></tr>';
                    return;
                }
                data.strategies.forEach(strategy => {
                    const row = strategiesTableBody.insertRow();
                    row.insertCell().textContent = strategy.id;
                    row.insertCell().textContent = strategy.name;
                    row.insertCell().textContent = strategy.category;
                    row.insertCell().textContent = strategy.risk_level;
                    row.insertCell().textContent = strategy.python_code_path;
                    
                    const actionsCell = row.insertCell();
                    const editButton = document.createElement('button');
                    editButton.className = 'btn btn-sm btn-outline';
                    editButton.textContent = 'Edit';
                    editButton.onclick = () => handleEditStrategy(strategy.id, strategy); // Pass current strategy data
                    actionsCell.appendChild(editButton);
                    // TODO: Add delete/disable buttons if strategies are DB managed
                });
            } else {
                throw new Error(data.message || "Failed to parse strategies list.");
            }
        } catch (error) {
            console.error("Error fetching admin strategies:", error);
            strategiesTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">Error loading strategies: ${error.message}</td></tr>`;
        }
    }

    async function handleEditStrategy(strategyId, currentStrategyData) { // Made async to use await
        // This would typically open a modal pre-filled with currentStrategyData
        // For now, just an alert and log.
        // alert(`Simulating edit for strategy ID: ${strategyId}.\nImplement modal/form for editing details like name, description, category, risk, file path, class name, default parameters JSON.`);
        // console.log("Current data for editing:", currentStrategyData);
        
        // On submit from modal:
        // Assuming you have a way to get updatedData from a form/modal
        const updatedData = { 
            name: currentStrategyData.name + " (Edited)", // Placeholder - replace with actual form data
            description: currentStrategyData.description, // Placeholder
            python_code_path: currentStrategyData.python_code_path, // Placeholder
            default_parameters: currentStrategyData.default_parameters, // Placeholder
            category: currentStrategyData.category, // Placeholder
            risk_level: currentStrategyData.risk_level // Placeholder
        };

        try {
           // Conceptual API: PUT /api/admin/strategies/{strategyId}
           const response = await fetch(`/api/v1/admin/strategies/${strategyId}`, {
               method: 'PUT',
               headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
               body: JSON.stringify(updatedData)
           });
           const result = await response.json();
           if (!response.ok) throw new Error(result.detail || result.message || `HTTP error! status: ${response.status}`);
           
           alert(result.message || "Strategy updated successfully.");
           fetchAdminStrategies(); // Refresh list
        } catch (error) { 
            console.error("Error updating strategy:", error);
            alert("Error updating strategy: " + error.message);
        }
    }

    if (addNewStrategyBtn) {
        addNewStrategyBtn.addEventListener('click', async () => { // Made async to use await
            // This would open a modal with a form to define a new strategy
            // alert("Simulating 'Add New Strategy'. Implement form/modal for defining new strategy (name, desc, path, class, category, risk, default_params_json).");
            // On submit from modal:
            // Assuming you have a way to get newStrategyData from a form/modal
            const newStrategyData = { 
                name: "New Strategy Name", // Placeholder - replace with actual form data
                description: "Description of new strategy", // Placeholder
                python_code_path: "path/to/new_strategy.py", // Placeholder
                default_parameters: {}, // Placeholder - replace with actual form data (JSON object)
                category: "Custom", // Placeholder
                risk_level: "Low" // Placeholder
            };
            
            try {
               // Conceptual API: POST /api/admin/strategies
               const response = await fetch('/api/v1/admin/strategies', {
                   method: 'POST',
                   headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` },
                   body: JSON.stringify(newStrategyData)
               });
               const result = await response.json();
               if (!response.ok) throw new Error(result.detail || result.message || `HTTP error! status: ${response.status}`);
               
               alert(result.message || "Strategy added successfully.");
               fetchAdminStrategies(); // Refresh list
            } catch (error) { 
                console.error("Error adding new strategy:", error);
                alert("Error adding strategy: " + error.message);
            }
        });
    }
    
    fetchAdminStrategies();
});
