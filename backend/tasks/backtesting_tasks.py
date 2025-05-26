import logging
import json
import importlib.util
import datetime
import time # Added for potential sleep in fetch_historical_data
import pandas as pd
import numpy as np # For equity curve calculation if needed
import ccxt

from sqlalchemy.orm import Session # For type hinting if needed, though tasks create their own sessions

from backend.celery_app import app
from backend.config import settings
from backend.models import BacktestResult, Strategy as StrategyModel # Using BacktestResult as it exists
from backend.services.strategy_service import _load_strategy_class_from_db_obj # For loading strategy
# from backend.services.exchange_service import fetch_historical_data # This will be defined locally or imported if made generic

logger = logging.getLogger(__name__)

# --- Dynamic DB Session Factory Import ---
def _import_db_session_factory(path_to_factory: str = "backend.db.SessionLocal"):
    """Dynamically imports the db_session_factory."""
    try:
        module_path, factory_name = path_to_factory.rsplit('.', 1)
        module = importlib.import_module(module_path)
        return getattr(module, factory_name)
    except Exception as e:
        logger.error(f"Failed to import db_session_factory from {path_to_factory}: {e}", exc_info=True)
        raise

# --- Historical Data Fetching (Task-Specific or Shared) ---
# This is a simplified version. A robust version might be in exchange_service.py
def _fetch_historical_data_for_task(exchange_id: str, symbol: str, timeframe: str, start_date: datetime.datetime, end_date: datetime.datetime) -> pd.DataFrame:
    logger.info(f"Fetching historical data: {exchange_id}, {symbol}, {timeframe}, from {start_date} to {end_date}")
    try:
        exchange_class = getattr(ccxt, exchange_id.lower())
        exchange = exchange_class({'enableRateLimit': True})
    except AttributeError:
        logger.error(f"Exchange {exchange_id} not found in CCXT.")
        raise ValueError(f"Unsupported exchange: {exchange_id}")
    except Exception as e:
        logger.error(f"Error initializing exchange {exchange_id}: {e}", exc_info=True)
        raise

    all_ohlcv = []
    since_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)
    limit = 500 # Max candles per request; adjust based on exchange

    while since_ms < end_ms:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since_ms, limit)
            if not ohlcv:
                break 
            
            # Filter candles strictly within the date range before extending
            # CCXT might return candles starting before `since_ms` or after `end_ms` depending on alignment.
            ohlcv_filtered = [c for c in ohlcv if c[0] >= since_ms and c[0] <= end_ms]
            all_ohlcv.extend(ohlcv_filtered)

            if not ohlcv_filtered or ohlcv[-1][0] >= end_ms:
                break # Reached or passed the end date

            since_ms = ohlcv[-1][0] + exchange.parse_timeframe(timeframe) * 1000 # Next starting point
            
        except ccxt.RateLimitExceeded as e:
            logger.warning(f"Rate limit exceeded: {e}. Retrying after delay...")
            time.sleep(exchange.rateLimit / 1000 + 1)
        except Exception as e:
            logger.error(f"Error fetching OHLCV data for {symbol} on {exchange_id}: {e}", exc_info=True)
            raise # Or handle more gracefully, e.g., by returning partial data if acceptable

    if not all_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    # Final filter to ensure data is strictly within the requested start and end (especially for the first/last candle)
    df = df[(df.index >= start_date) & (df.index <= end_date)]
    logger.info(f"Successfully fetched {len(df)} data points for {symbol} on {exchange_id}.")
    return df

# --- Core Backtesting Logic (Moved from BacktestingService) ---
MAX_BACKTEST_DAYS_TASK = 366 # Define within task context or load from settings

def _perform_backtest_logic_task(db_session: Session, # Expects an active DB session
                                 backtest_result_id: int,
                                 user_id: int, # For logging/context, not directly used for DB ops here if session is scoped
                                 strategy_id: int, # ID of the StrategyModel record
                                 custom_parameters: dict,
                                 symbol: str,
                                 timeframe: str,
                                 start_date_str: str,
                                 end_date_str: str,
                                 initial_capital: float = 10000.0,
                                 exchange_id: str = 'binance'
                                ):
    backtest_record = db_session.query(BacktestResult).filter(BacktestResult.id == backtest_result_id).first()
    if not backtest_record:
        logger.error(f"BacktestResult record with ID {backtest_result_id} not found in _perform_backtest_logic_task.")
        return # Cannot proceed or update status

    try:
        start_date = datetime.datetime.fromisoformat(start_date_str)
        end_date = datetime.datetime.fromisoformat(end_date_str)
    except ValueError:
        logger.error(f"Invalid date format for backtest ID {backtest_result_id}: {start_date_str} to {end_date_str}")
        backtest_record.status = "FAILED"
        backtest_record.results_json = json.dumps({"error": "Invalid date format."})
        db_session.commit()
        return

    if (end_date - start_date).days > MAX_BACKTEST_DAYS_TASK:
        msg = f"Backtest period cannot exceed {MAX_BACKTEST_DAYS_TASK} days."
        logger.error(f"{msg} (ID: {backtest_result_id})")
        backtest_record.status = "FAILED"
        backtest_record.results_json = json.dumps({"error": msg})
        db_session.commit()
        return
    if start_date >= end_date:
        msg = "Start date must be before end date."
        logger.error(f"{msg} (ID: {backtest_result_id})")
        backtest_record.status = "FAILED"
        backtest_record.results_json = json.dumps({"error": msg})
        db_session.commit()
        return

    # 1. Load Strategy Class
    strategy_db_obj = db_session.query(StrategyModel).filter(StrategyModel.id == strategy_id).first()
    if not strategy_db_obj:
        msg = f"Strategy with ID '{strategy_id}' not found."
        logger.error(f"{msg} (ID: {backtest_result_id})")
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return
    if not strategy_db_obj.is_active:
        msg = f"Strategy '{strategy_db_obj.name}' (ID: {strategy_id}) is not active."
        logger.error(f"{msg} (ID: {backtest_result_id})")
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return

    StrategyClass = _load_strategy_class_from_db_obj(strategy_db_obj) # Uses imported helper
    if not StrategyClass:
        msg = f"Could not load strategy class for {strategy_db_obj.name} (ID: {strategy_id})."
        logger.error(f"{msg} (ID: {backtest_result_id})")
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return

    backtest_record.strategy_name_used = strategy_db_obj.name # Store the actual name
    # db_session.commit() # Commit this small update or batch with next commit

    # 2. Fetch Historical Data
    try:
        historical_df = _fetch_historical_data_for_task(exchange_id, symbol, timeframe, start_date, end_date)
        if historical_df.empty:
            msg = "No historical data found for the given parameters."
            logger.warning(f"{msg} (ID: {backtest_result_id})")
            backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg, "detail": "No data from exchange."}); db_session.commit(); return
    except Exception as e:
        msg = f"Failed to fetch historical data: {str(e)}"
        logger.error(f"{msg} (ID: {backtest_result_id})", exc_info=True)
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return
    
    # 3. Instantiate Strategy
    # Ensure custom_parameters is a dict
    strategy_specific_settings = custom_parameters if isinstance(custom_parameters, dict) else json.loads(custom_parameters)

    init_params = {
        "name": strategy_db_obj.class_name, # Use class_name for instantiation
        "symbol": symbol,
        "timeframe": timeframe,
        "settings": strategy_specific_settings,
        "capital": strategy_specific_settings.get("capital", initial_capital) # Allow capital override from settings
    }
    try:
        strategy_instance = StrategyClass(**init_params)
    except Exception as e:
        msg = f"Error initializing strategy '{strategy_db_obj.name}': {str(e)}"
        logger.error(f"{msg} (ID: {backtest_result_id})", exc_info=True)
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return

    # 4. Run the strategy's backtest method
    try:
        # Assuming strategy_instance.run_backtest(data_df) returns a dict of results
        backtest_output = strategy_instance.run_backtest(historical_df) 
        if not isinstance(backtest_output, dict):
            logger.error(f"Strategy run_backtest for '{strategy_db_obj.name}' did not return a dictionary. Output: {type(backtest_output)}")
            msg = "Invalid output format from strategy backtest method."
            backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return
    except Exception as e:
        msg = f"Error during strategy's run_backtest method for '{strategy_db_obj.name}': {str(e)}"
        logger.error(f"{msg} (ID: {backtest_result_id})", exc_info=True)
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg}); db_session.commit(); return

    # 5. Process and Store Results
    # Example: pnl = backtest_output.get("pnl", 0.0) ... etc.
    # For now, storing the raw output as JSON
    try:
        backtest_record.results_json = json.dumps(backtest_output, default=str) # Use default=str for non-serializable types
    except TypeError as te:
        msg = f"Error serializing backtest results to JSON: {str(te)}"
        logger.error(f"{msg} (ID: {backtest_result_id})", exc_info=True)
        backtest_record.status = "FAILED"; backtest_record.results_json = json.dumps({"error": msg, "partial_results": str(backtest_output)}); db_session.commit(); return
        
    backtest_record.status = "COMPLETED"
    backtest_record.completed_at = datetime.datetime.utcnow()
    db_session.commit()
    logger.info(f"Backtest completed and results stored for ID: {backtest_result_id}.")


# --- Celery Task Definition ---
@app.task(bind=True)
def run_backtest_celery_task(self, # Changed name to avoid confusion with service method
                             backtest_result_id: int, # ID of the BacktestResult record
                             user_id: int,
                             strategy_id: int, # ID of the StrategyModel record
                             custom_parameters_json: str, # JSON string for strategy-specific settings
                             symbol: str,
                             timeframe: str,
                             start_date_str: str,
                             end_date_str: str,
                             initial_capital: float = 10000.0,
                             exchange_id: str = 'binance'
                            ):
    task_id = self.request.id
    logger.info(f"[Celery Task ID: {task_id}, DB Record ID: {backtest_result_id}] Received backtest task.")

    db_session_factory = _import_db_session_factory()
    db = db_session_factory()

    try:
        backtest_record = db.query(BacktestResult).filter(BacktestResult.id == backtest_result_id).first()
        if not backtest_record:
            logger.error(f"[Celery Task ID: {task_id}] BacktestResult record ID {backtest_result_id} not found. Aborting task.")
            return {"status": "error", "message": "BacktestResult record not found."}
        
        backtest_record.status = "RUNNING"
        backtest_record.celery_task_id = task_id # Store actual Celery task ID
        db.commit()
        logger.info(f"[Celery Task ID: {task_id}, DB Record ID: {backtest_result_id}] Status updated to RUNNING.")

        custom_parameters = json.loads(custom_parameters_json) if isinstance(custom_parameters_json, str) else custom_parameters_json

        # Call the core logic function
        _perform_backtest_logic_task(
            db_session=db, # Pass the active session
            backtest_result_id=backtest_result_id,
            user_id=user_id,
            strategy_id=strategy_id,
            custom_parameters=custom_parameters,
            symbol=symbol,
            timeframe=timeframe,
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            initial_capital=initial_capital,
            exchange_id=exchange_id
        )
        # _perform_backtest_logic_task handles updating the record to COMPLETED or FAILED internally.
        # Fetch the latest status after execution for the return message.
        db.refresh(backtest_record) # Refresh to get the latest status set by _perform_backtest_logic_task
        return {"status": backtest_record.status, "report_id": backtest_result_id, "message": f"Backtest task processed. Final status: {backtest_record.status}"}

    except Exception as e:
        logger.error(f"[Celery Task ID: {task_id}, DB Record ID: {backtest_result_id}] Unhandled error in Celery task: {e}", exc_info=True)
        if db.is_active:
            # Attempt to mark as FAILED if not already done by _perform_backtest_logic_task
            record_to_fail = db.query(BacktestResult).filter(BacktestResult.id == backtest_result_id).first()
            if record_to_fail and record_to_fail.status not in ["COMPLETED", "FAILED"]:
                record_to_fail.status = "FAILED"
                record_to_fail.results_json = json.dumps({"error": "Unhandled task error: " + str(e)}, default=str)
                record_to_fail.completed_at = datetime.datetime.utcnow()
                db.commit()
        return {"status": "error", "report_id": backtest_result_id, "message": f"Unhandled task error: {str(e)}"}
    finally:
        if db:
            db.close()
        logger.info(f"[Celery Task ID: {task_id}, DB Record ID: {backtest_result_id}] Celery task finalization.")
