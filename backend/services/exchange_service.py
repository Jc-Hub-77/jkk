# backend/services/exchange_service.py
import os
from typing import Optional # Added Optional
import ccxt
import json
import datetime
import logging # Add logging import
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session
from backend.models import ApiKey, User, UserStrategySubscription # Adjusted import path
from backend.config import settings # Import global settings

# Initialize logger
logger = logging.getLogger(__name__)

cipher_suite = None
if settings.API_ENCRYPTION_KEY: # Ensure API_ENCRYPTION_KEY is set
    try:
        # Ensure the key is 32 url-safe base64-encoded bytes
        # Fernet expects a URL-safe base64-encoded 32-byte key.
        # A dedicated API_ENCRYPTION_KEY, properly generated and securely stored, is REQUIRED for production.
        # This initialization assumes settings.API_ENCRYPTION_KEY is set and correctly formatted.
        # TODO: Implement a secure secrets management solution for API_ENCRYPTION_KEY in production.
        
        cipher_suite = Fernet(settings.API_ENCRYPTION_KEY.encode() if isinstance(settings.API_ENCRYPTION_KEY, str) else settings.API_ENCRYPTION_KEY)
        logger.info("API Key encryption cipher initialized (using configured API_ENCRYPTION_KEY).") # Use logger instead of print
    except Exception as e: # Catch more general errors from Fernet init
        logger.critical(f"Could not initialize Fernet cipher with configured API_ENCRYPTION_KEY: {e}. API key encryption/decryption will fail.", exc_info=True) # Use logger instead of print
        cipher_suite = None # Ensure cipher_suite is None on failure
else:
    # CRITICAL: No API_ENCRYPTION_KEY found in settings. API key encryption is REQUIRED for production.
    # A dedicated, randomly generated Fernet key stored securely (e.g., using a secrets management system) is required.
    cipher_suite = None
    logger.critical("No API_ENCRYPTION_KEY found in settings. API key encryption/decryption will fail.") # Use logger instead of print

def _encrypt_data(data: str) -> str:
    if not cipher_suite: raise ValueError("Encryption cipher not initialized. Cannot encrypt.")
    return cipher_suite.encrypt(data.encode()).decode()

def _decrypt_data(encrypted_data: str) -> str:
    if not cipher_suite: raise ValueError("Encryption cipher not initialized. Cannot decrypt.")
    try:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    except InvalidToken:
        print("Error: Decryption failed. Invalid token or key mismatch.")
        raise ValueError("Decryption failed. Ensure encryption key is correct and data is not corrupted.")

SUPPORTED_EXCHANGES = [exc.lower() for exc in ccxt.exchanges]

def add_exchange_api_key(db_session: Session, user_id: int, exchange_name: str, 
                         api_key_public: str, secret_key: str, passphrase: Optional[str] = None, label: Optional[str] = None):
    exchange_name_lower = exchange_name.lower()
    if exchange_name_lower not in SUPPORTED_EXCHANGES:
        return {"status": "error", "message": f"Exchange '{exchange_name}' is not supported."}

    user = db_session.query(User).filter(User.id == user_id).first()
    if not user: return {"status": "error", "message": "User not found."}

    if label: # Check for duplicate label for the same user and exchange
        existing_key_with_label = db_session.query(ApiKey).filter(
            ApiKey.user_id == user_id, ApiKey.exchange_name == exchange_name_lower, ApiKey.label == label
        ).first()
        if existing_key_with_label:
            return {"status": "error", "message": f"An API key with the label '{label}' already exists for {exchange_name}."}
    
    if not cipher_suite:
        return {"status": "error", "message": "System error: Encryption service not available."}

    try:
        encrypted_api_key_val = _encrypt_data(api_key_public)
        encrypted_secret_val = _encrypt_data(secret_key)
        encrypted_passphrase_val = _encrypt_data(passphrase) if passphrase else None # Corrected from _decrypt_data to _encrypt_data
    except ValueError as e:
        logger.error(f"Encryption error during add_exchange_api_key: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": f"Encryption error: {e}"}

    api_key_preview = api_key_public[:4] + "..." + api_key_public[-4:] if len(api_key_public) > 8 else api_key_public
    effective_label = label if label else f"{exchange_name.capitalize()} Key - {datetime.datetime.utcnow().strftime('%Y%m%d%H%M')}"


    new_api_key_entry = ApiKey(
        user_id=user_id,
        exchange_name=exchange_name_lower,
        label=effective_label,
        api_key_public_preview=api_key_preview,
        encrypted_api_key=encrypted_api_key_val,
        encrypted_secret_key=encrypted_secret_val,
        encrypted_passphrase=encrypted_passphrase_val,
        status="pending_verification",
        created_at=datetime.datetime.utcnow()
    )
    
    try:
        db_session.add(new_api_key_entry)
        db_session.commit()
        db_session.refresh(new_api_key_entry)
        return {
            "status": "success", 
            "message": f"API key for {exchange_name} added with label '{effective_label}'. Please test connectivity.",
            "api_key_id": new_api_key_entry.id
        }
    except Exception as e:
        db_session.rollback()
        logger.error(f"Error adding API key to DB: {e}", exc_info=True) # Use logger instead of print
        return {"status": "error", "message": "Database error while adding API key."}

def get_user_exchange_api_keys_display(db_session: Session, user_id: int):
    api_keys_query = db_session.query(ApiKey).filter(ApiKey.user_id == user_id).order_by(ApiKey.label).all()
    
    keys_display = []
    for key_entry in api_keys_query:
        keys_display.append({
            "id": key_entry.id,
            "exchange_name": key_entry.exchange_name.capitalize(),
            "label": key_entry.label,
            "api_key_preview": key_entry.api_key_public_preview,
            "status": key_entry.status,
            "status_message": key_entry.status_message,
            "last_tested_at": key_entry.last_tested_at.isoformat() if key_entry.last_tested_at else None,
            "created_at": key_entry.created_at.isoformat() if key_entry.created_at else None
        })
    return {"status": "success", "keys": keys_display}

def remove_exchange_api_key(db_session: Session, user_id: int, api_key_id: int):
    key_to_delete = db_session.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if not key_to_delete:
        return {"status": "error", "message": "API key not found or access denied."}
    
    # Check if key is used by active UserStrategySubscription
    active_subs = db_session.query(UserStrategySubscription).filter(
        UserStrategySubscription.api_key_id == api_key_id,
        UserStrategySubscription.is_active == True
    ).count()
    if active_subs > 0:
        return {"status": "error", "message": f"Cannot delete API key. It is used by {active_subs} active strategy subscription(s)."}

    try:
        db_session.delete(key_to_delete)
        db_session.commit()
        return {"status": "success", "message": "API key removed successfully."}
    except Exception as e:
        db_session.rollback()
        return {"status": "error", "message": "Database error while removing API key."}

def test_api_connectivity(db_session: Session, user_id: int, api_key_id: int):
    key_entry = db_session.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if not key_entry:
        return {"status": "error", "message": "API key not found or access denied."}

    if not cipher_suite:
        return {"status": "error", "message": "System error: Encryption service not available for testing."}

    original_status = key_entry.status
    key_entry.status = "testing"
    key_entry.status_message = "Attempting to connect to exchange..."
    key_entry.last_tested_at = datetime.datetime.utcnow()
    db_session.commit() # Commit status change before test

    try:
        decrypted_api_key = _decrypt_data(key_entry.encrypted_api_key)
        decrypted_secret_key = _decrypt_data(key_entry.encrypted_secret_key)
        decrypted_passphrase = _decrypt_data(key_entry.encrypted_passphrase) if key_entry.encrypted_passphrase else None
    except ValueError as e:
        key_entry.status = "error_decryption"
        key_entry.status_message = f"API key decryption failed: {e}. Please re-add the key."
        db_session.commit()
        return {"status": key_entry.status, "message": key_entry.status_message}
    
    exchange_name_lower = key_entry.exchange_name.lower()
    if not hasattr(ccxt, exchange_name_lower):
        key_entry.status = "error_exchange_unsupported"
        key_entry.status_message = f"Exchange '{exchange_name_lower}' is not supported by CCXT."
        db_session.commit()
        return {"status": key_entry.status, "message": key_entry.status_message}

    exchange_class = getattr(ccxt, exchange_name_lower)
    config = {'apiKey': decrypted_api_key, 'secret': decrypted_secret_key, 'options': {'adjustForTimeDifference': True}, 'enableRateLimit': True}
    if decrypted_passphrase: config['password'] = decrypted_passphrase

    exchange = exchange_class(config)
    
    try:
        markets = exchange.fetch_markets() # A common, usually safe call
        if markets:
            key_entry.status = "active"
            key_entry.status_message = "API connection successful. Market data fetched."
        else:
            key_entry.status = "error_test_failed"
            key_entry.status_message = "API connection test failed: No market data returned."
            
    except ccxt.AuthenticationError as e:
        key_entry.status = "error_authentication"
        key_entry.status_message = f"Authentication failed: {str(e)}. Check API key permissions and validity."
    except ccxt.NetworkError as e:
        key_entry.status = "error_network" 
        key_entry.status_message = f"Network error: {str(e)}. Check connectivity or try again later."
    except ccxt.ExchangeError as e: 
        key_entry.status = "error_exchange"
        key_entry.status_message = f"Exchange error: {str(e)}."
    except Exception as e: 
        key_entry.status = "error_unknown"
        key_entry.status_message = f"An unexpected error occurred: {str(e)}."
    
    try:
        db_session.commit()
    except Exception as e_db:
        db_session.rollback()
        # Return test outcome even if DB save fails, but note the DB issue
        return {"status": key_entry.status, "message": key_entry.status_message + f" (DB status update failed: {e_db})"}
        
    return {"status": key_entry.status, "message": key_entry.status_message, "api_key_id": key_entry.id}

# Placeholder for fetching exchange client instance
def get_exchange_client(db_session: Session, api_key_id: int, user_id: int) -> Optional[ccxt.Exchange]:
    key_entry = db_session.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if not key_entry or key_entry.status != "active":
        logger.warning(f"Cannot get exchange client: API key ID {api_key_id} not found, not active, or doesn't belong to user {user_id}.") # Use logger instead of print
        return None
    
    if not cipher_suite:
        logger.error("Cannot get exchange client: Encryption service not available.") # Use logger instead of print
        return None

    try:
        decrypted_api_key = _decrypt_data(key_entry.encrypted_api_key)
        decrypted_secret_key = _decrypt_data(key_entry.encrypted_secret_key)
        decrypted_passphrase = _decrypt_data(key_entry.encrypted_passphrase) if key_entry.encrypted_passphrase else None
    except ValueError:
        logger.error(f"Decryption failed for API key ID {api_key_id}. Cannot initialize client.", exc_info=True) # Use logger instead of print
        return None

    exchange_name_lower = key_entry.exchange_name.lower()
    if not hasattr(ccxt, exchange_name_lower):
        logger.error(f"Exchange '{exchange_name_lower}' not supported by CCXT.") # Use logger instead of print
        return None

    exchange_class = getattr(ccxt, exchange_name_lower)
    config = {'apiKey': decrypted_api_key, 'secret': decrypted_secret_key, 'options': {'adjustForTimeDifference': True}, 'enableRateLimit': True}
    if decrypted_passphrase: config['password'] = decrypted_passphrase
    
    return exchange_class(config)

def get_encrypted_api_key_data(db_session: Session, api_key_id: int, user_id: int) -> Optional[dict]:
    """
    Retrieves encrypted API key data for a given API key ID and user ID.
    """
    key_entry = db_session.query(ApiKey).filter(ApiKey.id == api_key_id, ApiKey.user_id == user_id).first()
    if not key_entry:
        logger.warning(f"Encrypted API key data not found for API key ID {api_key_id}, user ID {user_id}.")
        return None

    # Ensure that we have the necessary encrypted fields.
    if not key_entry.encrypted_api_key or not key_entry.encrypted_secret_key:
        logger.error(f"API key ID {api_key_id} for user {user_id} is missing encrypted key/secret.")
        return None # Or raise an error, depending on desired handling

    return {
        "api_key": key_entry.encrypted_api_key,
        "secret_key": key_entry.encrypted_secret_key,
        "passphrase": key_entry.encrypted_passphrase, # This can be None
        "exchange_id": key_entry.exchange_name.lower(),
        "label": key_entry.label # For logging/reference
    }

def fetch_historical_data(exchange_id: str, symbol: str, timeframe: str, start_date: datetime.datetime, end_date: datetime.datetime):
    """
    Fetches historical OHLCV data for a given symbol and timeframe from an exchange.
    Returns data as a pandas DataFrame.
    """
    # This function seems to be missing pandas (pd) and time imports if used standalone.
    # For now, assuming they are available in the broader context or will be added if this function is called.
    import pandas as pd # Added import
    import time # Added import

    exchange_id_lower = exchange_id.lower()
    if exchange_id_lower not in SUPPORTED_EXCHANGES:
        logger.error(f"Exchange '{exchange_id}' is not supported for historical data fetching.")
        return pd.DataFrame()

    try:
        exchange_class = getattr(ccxt, exchange_id_lower)
        # Initialize exchange without API keys for public data
        exchange = exchange_class({'enableRateLimit': True})
        logger.info(f"Initialized CCXT exchange '{exchange_id}' for historical data.")

    except Exception as e:
        logger.error(f"Failed to initialize CCXT exchange '{exchange_id}' for historical data: {e}", exc_info=True)
        return pd.DataFrame()

    all_ohlcv = []
    # CCXT fetch_ohlcv uses milliseconds timestamp for 'since'
    since_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)
    limit = 1000 # Max number of candles per request for many exchanges

    logger.info(f"Fetching historical data for {symbol}@{timeframe} on {exchange_id} from {start_date} to {end_date}.")

    while since_ms < end_ms:
        try:
            # Fetch data from 'since_ms' up to 'limit' candles
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since_ms, limit)

            if not ohlcv:
                logger.info(f"No more historical data found for {symbol}@{timeframe} from {exchange_id} starting at {datetime.datetime.fromtimestamp(since_ms / 1000)}.")
                break # No more data

            all_ohlcv.extend(ohlcv)

            # Update since_ms to the timestamp of the last fetched candle + 1 unit of timeframe
            # This is a simplified approach; a more robust method calculates the next timestamp
            # based on the timeframe duration. For now, use the timestamp of the last candle.
            last_timestamp = ohlcv[-1][0]
            since_ms = last_timestamp + exchange.parse_timeframe(timeframe) * 1000 # Add timeframe duration in ms

            # Optional: Add a small delay to respect rate limits, even with enableRateLimit=True
            # time.sleep(exchange.rateLimit / 1000)

        except ccxt.RateLimitExceeded as e:
            logger.warning(f"Rate limit exceeded while fetching historical data from {exchange_id}. Retrying after delay.", exc_info=True)
            time.sleep(exchange.rateLimit / 1000 + 1) # Wait a bit longer
            continue # Retry the same 'since_ms'

        except ccxt.BaseError as e:
            logger.error(f"CCXT error fetching historical data for {symbol}@{timeframe} on {exchange_id}: {e}", exc_info=True)
            # Depending on the error, you might want to break or retry
            break
        except Exception as e:
            logger.error(f"Unexpected error fetching historical data for {symbol}@{timeframe} on {exchange_id}: {e}", exc_info=True)
            break

    if not all_ohlcv:
        logger.warning(f"No historical data fetched for {symbol}@{timeframe} on {exchange_id} in the specified range.")
        return pd.DataFrame()

    # Convert to pandas DataFrame
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    # Filter by end_date to ensure we don't include data beyond the requested period
    df = df[df.index <= end_date]

    logger.info(f"Successfully fetched {len(df)} historical data points for {symbol}@{timeframe} on {exchange_id}.")
    return df
