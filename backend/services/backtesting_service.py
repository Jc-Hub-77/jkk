# backend/services/backtesting_service.py
import datetime
import json
import logging

from sqlalchemy.orm import Session
from backend.models import BacktestResult, Strategy as StrategyModel, User # Assuming User model is needed for validation
from backend.celery_app import celery_app 
# Import the renamed Celery task from the correct module
from backend.tasks.backtesting_tasks import run_backtest_celery_task 

# --- Configuration ---
MAX_BACKTEST_DAYS = 366 # Maximum backtest period allowed (e.g., 1 year)

# Initialize logger
logger = logging.getLogger(__name__)

# _perform_backtest_logic has been moved to backend/tasks/backtesting_tasks.py

# --- Service Functions ---
def submit_backtest_task(db_session: Session,
                         user_id: int,
                         strategy_id: int,
                         custom_parameters: dict, # This should be a dict
                         symbol: str,
                         timeframe: str,
                         start_date_str: str,
                         end_date_str: str,
                         initial_capital: float = 10000.0,
                         exchange_id: str = 'binance'
                        ):
    """
    Submits a backtest task to be run by a Celery worker.
    Creates a BacktestResult record with 'PENDING' status.
    """
    # Validate user
    user = db_session.query(User).filter(User.id == user_id).first()
    if not user:
        return {"status": "error", "message": "User not found."}

    # Validate strategy
    strategy = db_session.query(StrategyModel).filter(StrategyModel.id == strategy_id).first()
    if not strategy:
        return {"status": "error", "message": f"Strategy with ID {strategy_id} not found."}
    if not strategy.is_active:
        return {"status": "error", "message": f"Strategy '{strategy.name}' is not active."}


    # Basic date validation before queuing
    try:
        start_date = datetime.datetime.fromisoformat(start_date_str)
        end_date = datetime.datetime.fromisoformat(end_date_str)
    except ValueError:
        return {"status": "error", "message": "Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)."}

    if (end_date - start_date).days > MAX_BACKTEST_DAYS: # Using MAX_BACKTEST_DAYS from service config
        return {"status": "error", "message": f"Backtest period cannot exceed {MAX_BACKTEST_DAYS} days."}
    if start_date >= end_date:
        return {"status": "error", "message": "Start date must be before end date."}

    # Create a BacktestResult record in the DB with status 'PENDING'
    # Ensure custom_parameters is stored as a JSON string
    custom_parameters_json = json.dumps(custom_parameters) if isinstance(custom_parameters, dict) else custom_parameters
    
    backtest_record = BacktestResult(
        user_id=user_id,
        strategy_name_used=strategy.name, # Use actual strategy name
        # strategy_code_snapshot might be populated later if needed
        custom_parameters_json=custom_parameters_json,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        symbol=symbol,
        status="PENDING" # Initial status
    )
    db_session.add(backtest_record)
    db_session.commit()
    db_session.refresh(backtest_record)

    try:
        # Send the task to the Celery queue
        # Parameters match the run_backtest_celery_task signature
        task_args = {
            "backtest_result_id": backtest_record.id,
            "user_id": user_id,
            "strategy_id": strategy_id, # Pass strategy_id for the task to load StrategyModel
            "custom_parameters_json": custom_parameters_json,
            "symbol": symbol,
            "timeframe": timeframe,
            "start_date_str": start_date_str,
            "end_date_str": end_date_str,
            "initial_capital": initial_capital,
            "exchange_id": exchange_id
        }
        task = run_backtest_celery_task.delay(**task_args)
        
        logger.info(f"Queued backtest task for user {user_id}, strategy {strategy_id}. Record ID: {backtest_record.id}, Celery Task ID: {task.id}")

        # Update the BacktestResult record with the Celery task ID
        backtest_record.celery_task_id = task.id
        db_session.commit()

        return {"status": "success", "message": "Backtest task queued.", "backtest_id": backtest_record.id, "task_id": task.id}

    except Exception as e:
        # If task queuing fails, mark the record as FAILED_TO_QUEUE
        logger.error(f"Failed to queue backtest task for user {user_id}, strategy {strategy_id}, record {backtest_record.id}: {e}", exc_info=True)
        backtest_record.status = "FAILED_TO_QUEUE" # More specific status
        backtest_record.results_json = json.dumps({"error": f"Failed to queue Celery task: {str(e)}"})
        db_session.commit()
        return {"status": "error", "message": f"Failed to queue backtest task: {e}"}

def get_backtest_status(db_session: Session, backtest_id: int, user_id: int):
    """
    Retrieves the status of a specific backtest for a user.
    """
    backtest_record = db_session.query(BacktestResult).filter(
        BacktestResult.id == backtest_id,
        BacktestResult.user_id == user_id # Ensure user owns this backtest
    ).first()

    if not backtest_record:
        return {"status": "error", "message": "Backtest not found or access denied."}
    
    return {
        "status": "success",
        "backtest_id": backtest_record.id,
        "current_status": backtest_record.status,
        "strategy_name": backtest_record.strategy_name_used,
        "symbol": backtest_record.symbol,
        "timeframe": backtest_record.timeframe,
        "celery_task_id": backtest_record.celery_task_id,
        "created_at": backtest_record.created_at.isoformat(),
        "updated_at": backtest_record.updated_at.isoformat(),
        "completed_at": backtest_record.completed_at.isoformat() if backtest_record.completed_at else None,
    }

def get_backtest_results(db_session: Session, backtest_id: int, user_id: int):
    """
    Retrieves the full results of a completed backtest for a user.
    """
    backtest_record = db_session.query(BacktestResult).filter(
        BacktestResult.id == backtest_id,
        BacktestResult.user_id == user_id
    ).first()

    if not backtest_record:
        return {"status": "error", "message": "Backtest not found or access denied."}
    
    if backtest_record.status != "COMPLETED":
        return {
            "status": "info", 
            "message": f"Backtest is not yet completed. Current status: {backtest_record.status}",
            "backtest_id": backtest_record.id,
            "current_status": backtest_record.status
        }
        
    results = None
    try:
        results = json.loads(backtest_record.results_json) if backtest_record.results_json else {}
    except json.JSONDecodeError:
        logger.error(f"Error decoding results_json for backtest ID {backtest_id}: {backtest_record.results_json}")
        return {"status": "error", "message": "Error decoding results from storage."}

    return {
        "status": "success",
        "backtest_id": backtest_record.id,
        "strategy_name": backtest_record.strategy_name_used,
        "symbol": backtest_record.symbol,
        "timeframe": backtest_record.timeframe,
        "start_date": backtest_record.start_date.isoformat(),
        "end_date": backtest_record.end_date.isoformat(),
        "status": backtest_record.status,
        "results": results, # Parsed JSON results
        "created_at": backtest_record.created_at.isoformat(),
        "completed_at": backtest_record.completed_at.isoformat() if backtest_record.completed_at else None,
    }

def list_user_backtests(db_session: Session, user_id: int, skip: int = 0, limit: int = 100):
    """
    Lists all backtests submitted by a user.
    """
    query = db_session.query(BacktestResult).filter(BacktestResult.user_id == user_id).order_by(BacktestResult.created_at.desc())
    total_count = query.count()
    backtests = query.offset(skip).limit(limit).all()

    return {
        "status": "success",
        "total_count": total_count,
        "skip": skip,
        "limit": limit,
        "backtests": [
            {
                "id": br.id,
                "strategy_name": br.strategy_name_used,
                "symbol": br.symbol,
                "timeframe": br.timeframe,
                "status": br.status,
                "created_at": br.created_at.isoformat(),
                "completed_at": br.completed_at.isoformat() if br.completed_at else None,
            } for br in backtests
        ]
    }

# TODO: Add service functions to cancel a PENDING or RUNNING backtest (revoke Celery task and update status).
# TODO: Add service functions for admin to view/manage all backtests.
