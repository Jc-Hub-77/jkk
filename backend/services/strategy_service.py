# backend/services/strategy_service.py
import os
import importlib.util
import json
import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc 
from backend.models import Strategy as StrategyModel, UserStrategySubscription, User, ApiKey # Renamed to avoid conflict
from backend.config import settings
from backend.services import live_trading_service # Import live trading service for deployment
from typing import Optional, List, Dict, Any

STRATEGIES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'strategies') # Adjusted path

def _load_strategy_class_from_db_obj(strategy_db_obj: StrategyModel):
    """Dynamically loads a strategy class from its file path stored in the DB object."""
    if not strategy_db_obj.python_code_path:
        print(f"Error: Strategy DB object ID {strategy_db_obj.id} has no python_code_path.")
        return None
    
    file_path = os.path.join(STRATEGIES_DIR, strategy_db_obj.python_code_path)
    # Assume class_name is derived or stored. For now, let's try to infer or require it.
    # A good practice would be to store class_name in the StrategyModel.
    # For this example, let's assume a convention or a field like 'class_name_in_file'.
    # If StrategyModel had a 'class_name' field:
    # class_name = strategy_db_obj.class_name 
    # If not, we might infer it from python_code_path (e.g., MyStrategyFile.py -> MyStrategyFile)
    # This is brittle. It's better to store the class name explicitly in the Strategy model.
    # For now, let's assume a convention: file_name_strategy.py -> FileNameStrategy
    
    module_name_from_path = strategy_db_obj.python_code_path.replace('.py', '')
    # Attempt to find a class name, e.g. if python_code_path is 'ema_crossover_strategy.py', look for 'EMACrossoverStrategy'
    # This is a common convention but not guaranteed.
    # A 'main_class_name' field in StrategyModel would be much better.
    # For now, we'll try a simple transformation, but this should be improved.
    assumed_class_name = "".join(word.capitalize() for word in module_name_from_path.split('_'))
    if not assumed_class_name.endswith("Strategy"): # Ensure it ends with Strategy if that's the convention
        if "strategy" in assumed_class_name.lower(): # if "strategy" is part of the name but not at the end
             # find "strategy" and capitalize it, e.g. EmaCrossoverStrategy
             idx = assumed_class_name.lower().find("strategy")
             assumed_class_name = assumed_class_name[:idx] + "Strategy" # Simplistic
        else: # if "strategy" is not in the name at all
            assumed_class_name += "Strategy"


    if not os.path.exists(file_path):
        print(f"Error: Strategy file not found at {file_path} for strategy '{strategy_db_obj.name}'.")
        return None
        
    spec = importlib.util.spec_from_file_location(module_name_from_path, file_path)
    if spec is None:
        print(f"Error: Could not load spec for strategy module {module_name_from_path} at {file_path}.")
        return None
    
    strategy_module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(strategy_module)
        # Try to get the class. This needs a reliable way to know the class name.
        # Using assumed_class_name. This is a point of potential failure.
        loaded_class = getattr(strategy_module, assumed_class_name, None)
        if not loaded_class:
            print(f"Warning: Assumed class name '{assumed_class_name}' not found in {file_path}. Trying common names.")
            # Fallback: iterate through module attributes to find a class that might be it (e.g., ends with 'Strategy')
            for attr_name in dir(strategy_module):
                attr = getattr(strategy_module, attr_name)
                if isinstance(attr, type) and attr_name.endswith("Strategy") and attr_name != "BaseStrategy": # Exclude a common base
                    print(f"Found potential strategy class by convention: {attr_name}")
                    return attr # Return the first one found by convention
            print(f"Error: Could not find a suitable strategy class in {file_path} for strategy '{strategy_db_obj.name}'. Tried '{assumed_class_name}'.")
            return None
        return loaded_class
    except Exception as e:
        print(f"Error loading strategy module {module_name_from_path} for strategy '{strategy_db_obj.name}': {e}")
        return None

def list_available_strategies(db_session: Session) -> Dict[str, Any]:
    """Lists all active strategies available to users from the database."""
    try:
        strategies_from_db = db_session.query(StrategyModel).filter(StrategyModel.is_active == True).order_by(StrategyModel.name).all()
        
        available_strategies_data = []
        for s_db in strategies_from_db:
            available_strategies_data.append({
                "id": s_db.id, # Use DB id
                "name": s_db.name, 
                "description": s_db.description,
                "category": s_db.category, 
                "risk_level": s_db.risk_level,
                "historical_performance_summary": s_db.historical_performance_summary
                # Add other fields as needed for the listing
            })
        return {"status": "success", "strategies": available_strategies_data}
    except Exception as e:
        print(f"Error listing available strategies from DB: {e}")
        return {"status": "error", "message": "Could not retrieve strategies."}


def get_strategy_details(db_session: Session, strategy_db_id: int) -> Dict[str, Any]:
    """Gets detailed information about a specific strategy from DB, including its parameters."""
    strategy_db_obj = db_session.query(StrategyModel).filter(StrategyModel.id == strategy_db_id, StrategyModel.is_active == True).first()
    if not strategy_db_obj:
        return {"status": "error", "message": "Active strategy not found or ID is invalid."}

    StrategyClass = _load_strategy_class_from_db_obj(strategy_db_obj)
    if not StrategyClass:
        return {"status": "error", "message": f"Could not load strategy class for '{strategy_db_obj.name}'."}
    
    params_def = {}
    # Assume a static/class method 'get_parameters_definition' exists on the loaded class
    # This method should define the parameters the strategy accepts.
    # Example: {"param_name": {"type": "int", "default": 10, "label": "My Parameter"}}
    default_params_method_name = "get_parameters_definition" # Standardize this method name
    try:
        if hasattr(StrategyClass, default_params_method_name):
            method_to_call = getattr(StrategyClass, default_params_method_name)
            params_def = method_to_call() 
        else:
            print(f"Warning: Strategy class {StrategyClass.__name__} does not have '{default_params_method_name}' method.")
            params_def = json.loads(strategy_db_obj.default_parameters) if strategy_db_obj.default_parameters else {}
            if not params_def: # If default_parameters is also empty/invalid
                 params_def = {"info": "No parameter definition method found and no default parameters in DB."}

    except Exception as e:
        print(f"Error getting parameter definition for strategy '{strategy_db_obj.name}': {e}")
        params_def = {"error": f"Could not load parameter definitions: {str(e)}"}

    details = {
        "id": strategy_db_obj.id, 
        "name": strategy_db_obj.name, 
        "description": strategy_db_obj.description,
        "category": strategy_db_obj.category, 
        "risk_level": strategy_db_obj.risk_level,
        "parameters_definition": params_def, # This should be the structure defining params
        "default_parameters_db": json.loads(strategy_db_obj.default_parameters) if strategy_db_obj.default_parameters else {} # Actual defaults from DB
    }
    return {"status": "success", "details": details}


def create_or_update_strategy_subscription(db_session: Session, user_id: int, strategy_db_id: int, 
                                           api_key_id: int, custom_parameters: dict, 
                                           subscription_months: int = 1): # payment_transaction_id removed for now
    user = db_session.query(User).filter(User.id == user_id).first()
    if not user: return {"status": "error", "message": "User not found."}
    
    strategy_db_obj = db_session.query(StrategyModel).filter(StrategyModel.id == strategy_db_id, StrategyModel.is_active == True).first()
    if not strategy_db_obj: return {"status": "error", "message": "Active strategy not found."}
    
    api_key = db_session.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if not api_key: return {"status": "error", "message": "API key not found or does not belong to user."}
    if api_key.status != "active": return {"status": "error", "message": "Selected API key is not active."}

    existing_sub = db_session.query(UserStrategySubscription).filter(
        UserStrategySubscription.user_id == user_id,
        UserStrategySubscription.strategy_id == strategy_db_id, # Use DB ID
        UserStrategySubscription.api_key_id == api_key_id 
    ).order_by(desc(UserStrategySubscription.expires_at)).first()

    now = datetime.datetime.utcnow()
    
    if existing_sub:
        current_expiry = existing_sub.expires_at if existing_sub.expires_at else now
        start_from = max(now, current_expiry) 
        new_expiry = start_from + datetime.timedelta(days=30 * subscription_months) # Simplified month
        
        existing_sub.expires_at = new_expiry
        existing_sub.is_active = True # Ensure it's active on extension
        existing_sub.custom_parameters = json.dumps(custom_parameters)
        subscribed_item = existing_sub
        action_message = "Subscription extended"
    else:
        new_expiry = now + datetime.timedelta(days=30 * subscription_months)
        new_subscription = UserStrategySubscription(
            user_id=user_id, strategy_id=strategy_db_id, api_key_id=api_key_id,
            custom_parameters=json.dumps(custom_parameters),
            is_active=True, subscribed_at=now, expires_at=new_expiry
        )
        db_session.add(new_subscription)
        subscribed_item = new_subscription
        action_message = "Subscription created"
    
    try:
        db_session.commit()
        db_session.refresh(subscribed_item)
        # Trigger deployment of this strategy instance
        deployment_result = live_trading_service.deploy_strategy(db_session, subscribed_item.id)

        # Optionally update subscription status based on deployment result
        if deployment_result["status"] == "error":
             subscribed_item.status_message = f"Subscription created, but deployment failed: {deployment_result['message']}"
             subscribed_item.is_active = False # Mark as inactive if deployment fails
             db_session.commit() # Commit the status update

        return {
            "status": "success", # Return success for subscription creation/update regardless of immediate deployment outcome
            "message": f"{action_message} for '{strategy_db_obj.name}'. It is now active.",
            "subscription_id": subscribed_item.id,
            "expires_at": subscribed_item.expires_at.isoformat()
        }
    except Exception as e:
        db_session.rollback()
        print(f"Error during subscription DB commit for strategy '{strategy_db_obj.name}': {e}")
        return {"status": "error", "message": "Database error during subscription processing."}


def list_user_subscriptions(db_session: Session, user_id: int) -> Dict[str, Any]:
    subscriptions = db_session.query(UserStrategySubscription).filter(
        UserStrategySubscription.user_id == user_id
    ).join(StrategyModel).options(sqlalchemy.orm.joinedload(UserStrategySubscription.strategy)).order_by(desc(UserStrategySubscription.expires_at)).all()
    # Eager load strategy details

    user_subs_display = []
    now = datetime.datetime.utcnow()
    for sub in subscriptions:
        strategy_info = sub.strategy # Access the joined StrategyModel object
        is_currently_active = sub.is_active and (sub.expires_at > now if sub.expires_at else True)
        
        time_remaining_seconds = 0
        if sub.expires_at and sub.expires_at > now:
            time_remaining_seconds = (sub.expires_at - now).total_seconds()

        user_subs_display.append({
            "subscription_id": sub.id,
            "strategy_id": sub.strategy_id, # This is the DB ID
            "strategy_name": strategy_info.name if strategy_info else "Unknown Strategy",
            "api_key_id": sub.api_key_id, 
            "custom_parameters": json.loads(sub.custom_parameters) if isinstance(sub.custom_parameters, str) else sub.custom_parameters,
            "is_active": is_currently_active,
            "status_message": sub.status_message,
            "subscribed_at": sub.subscribed_at.isoformat() if sub.subscribed_at else None,
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else "Never (or lifetime)",
            "time_remaining_seconds": int(time_remaining_seconds)
        })
    return {"status": "success", "subscriptions": user_subs_display}

# Need to import sqlalchemy for joinedload
import sqlalchemy.orm

# TODO: Function to deactivate/cancel a subscription by user or admin
# TODO: Function for admin to manually trigger/update subscription status (e.g., after manual payment)
