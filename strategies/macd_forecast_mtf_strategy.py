# trading_platform/strategies/macd_forecast_mtf_strategy.py
import datetime
import logging
import pandas as pd
import ta # For MACD and other indicators
import numpy as np # For percentile calculations
 
# Note: CCXT interactions will be handled by the backtesting engine or live runner
# which will pass an initialized exchange object or historical data.
 
logger = logging.getLogger(__name__)

# Helper for PineScript-like percentile_linear_interpolation
def percentile_linear_interpolation(data_array, percentile):
    if not data_array:
        return 0.0 # Or handle as an error/None
    return np.percentile(np.array(data_array), percentile, method='linear' if hasattr(np, 'percentile') and 'method' in np.percentile.__code__.co_varnames else 'linear')


class MACDForecastMTFStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 1000,
                 # Timeframe settings
                 htf: str = "240", # Higher timeframe for trend
                 # MACD settings
                 fast_len: int = 12,
                 slow_len: int = 26,
                 signal_len: int = 9,
                 trend_determination: str = 'MACD - Signal', # 'MACD' or 'MACD - Signal'
                 # Strategy Parameters
                 lot_size: float = 1.0, # This might be interpreted as risk unit or fixed quantity
                 use_stop_loss: bool = True,
                 stop_loss_percent: float = 2.0,
                 use_take_profit: bool = True,
                 take_profit_percent: float = 4.0,
                 # Forecast Settings
                 max_memory: int = 50, # Max length of price delta vectors
                 forecast_length: int = 100, # How many bars ahead to forecast
                 forecast_top_percentile: int = 80,
                 forecast_mid_percentile: int = 50,
                 forecast_bottom_percentile: int = 20
                 ):
        self.name = "MTF MACD Strategy with Forecasting"
        self.symbol = symbol
        self.timeframe = timeframe # Primary timeframe for execution
        self.capital = capital # Initial capital for backtesting or allocation for live

        self.htf = htf
        self.fast_len = fast_len
        self.slow_len = slow_len
        self.signal_len = signal_len
        self.trend_determination = trend_determination
        
        self.lot_size = lot_size # How this is used needs clarification (fixed qty or risk based)
        self.use_stop_loss = use_stop_loss
        self.stop_loss_percent = stop_loss_percent / 100.0 # Convert to decimal
        self.use_take_profit = use_take_profit
        self.take_profit_percent = take_profit_percent / 100.0 # Convert to decimal

        self.max_memory = max_memory
        self.forecast_length = forecast_length
        self.forecast_top_percentile = forecast_top_percentile
        self.forecast_mid_percentile = forecast_mid_percentile
        self.forecast_bottom_percentile = forecast_bottom_percentile

        # Internal state for forecasting (complex to manage like PineScript's 'var')
        # These would be dictionaries or lists of lists to simulate 'vector' and 'holder'
        self.forecast_memory_up = {} # Key: up_idx, Value: list of price deltas (vector)
        self.forecast_memory_down = {} # Key: dn_idx, Value: list of price deltas (vector)
        self.up_idx_counter = 0 # Simulates PineScript's up_idx
        self.dn_idx_counter = 0 # Simulates PineScript's dn_idx
        self.uptrend_init_price = None
        self.downtrend_init_price = None
        
        self.price_precision = 8 # Default, should be updated from exchange
        self.quantity_precision = 8 # Default

        logger.info(f"Initialized {self.name} for {self.symbol} on {self.timeframe} (HTF: {self.htf})")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "htf": {"type": "timeframe", "default": "240", "label": "Higher Timeframe (Trend)"},
            "fast_len": {"type": "int", "default": 12, "min": 2, "label": "MACD Fast Length"},
            "slow_len": {"type": "int", "default": 26, "min": 2, "label": "MACD Slow Length"},
            "signal_len": {"type": "int", "default": 9, "min": 2, "label": "MACD Signal Length"},
            "trend_determination": {"type": "select", "default": "MACD - Signal", "options": ["MACD", "MACD - Signal"], "label": "Trend Determination (HTF)"},
            "lot_size": {"type": "float", "default": 1.0, "min": 0.000001, "label": "Position Size (e.g., contracts, coins)"},
            "use_stop_loss": {"type": "bool", "default": True, "label": "Use Stop Loss"},
            "stop_loss_percent": {"type": "float", "default": 2.0, "min": 0.1, "step": 0.1, "label": "Stop Loss %"},
            "use_take_profit": {"type": "bool", "default": True, "label": "Use Take Profit"},
            "take_profit_percent": {"type": "float", "default": 4.0, "min": 0.1, "step": 0.1, "label": "Take Profit %"},
            "max_memory": {"type": "int", "default": 50, "min": 2, "label": "Forecast Max Memory (bars)"},
            "forecast_length": {"type": "int", "default": 100, "min": 1, "label": "Forecast Projection Length (bars)"},
            "forecast_top_percentile": {"type": "int", "default": 80, "min": 51, "max": 99, "label": "Forecast Top Percentile"},
            "forecast_mid_percentile": {"type": "int", "default": 50, "min": 1, "max": 99, "label": "Forecast Mid Percentile"},
            "forecast_bottom_percentile": {"type": "int", "default": 20, "min": 1, "max": 49, "label": "Forecast Bottom Percentile"}
        }

    def _calculate_macd(self, series_df, fast, slow, signal):
        # Ensure 'close' column exists
        if 'close' not in series_df.columns:
            raise ValueError("DataFrame must contain 'close' column for MACD calculation.")
        
        # Calculate MACD using pandas_ta
        macd_df = series_df.ta.macd(fast=fast, slow=slow, signal=signal, append=False) # Use append=False to get a df
        if macd_df is None or macd_df.empty:
            # Create empty DataFrame with expected columns if calculation fails or returns None
            return pd.DataFrame(columns=[f'MACD_{fast}_{slow}_{signal}', f'MACDh_{fast}_{slow}_{signal}', f'MACDs_{fast}_{slow}_{signal}'])

        # Rename columns to be generic for easier access (e.g., 'macd', 'signal', 'histogram')
        # pandas_ta typically names them like MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
        return macd_df.rename(columns={
            f'MACD_{fast}_{slow}_{signal}': 'macd',
            f'MACDh_{fast}_{slow}_{signal}': 'histogram',
            f'MACDs_{fast}_{slow}_{signal}': 'signal'
        })

    # --- Forecasting Methods (Python translation of PineScript logic) ---
    def _populate_forecast_memory(self, is_uptrend_primary, current_close_price):
        """Simulates PineScript's 'populate' method for forecast memory."""
        if is_uptrend_primary:
            if self.uptrend_init_price is None: # Should have been set on trend start
                logger.warning("Uptrend init price not set for forecast memory population.")
                return
            
            price_delta = current_close_price - self.uptrend_init_price
            if self.up_idx_counter not in self.forecast_memory_up:
                self.forecast_memory_up[self.up_idx_counter] = []
            
            self.forecast_memory_up[self.up_idx_counter].insert(0, price_delta) # unshift
            if len(self.forecast_memory_up[self.up_idx_counter]) > self.max_memory:
                self.forecast_memory_up[self.up_idx_counter].pop()
        else: # Downtrend
            if self.downtrend_init_price is None:
                logger.warning("Downtrend init price not set for forecast memory population.")
                return

            price_delta = current_close_price - self.downtrend_init_price
            if self.dn_idx_counter not in self.forecast_memory_down:
                self.forecast_memory_down[self.dn_idx_counter] = []

            self.forecast_memory_down[self.dn_idx_counter].insert(0, price_delta) # unshift
            if len(self.forecast_memory_down[self.dn_idx_counter]) > self.max_memory:
                self.forecast_memory_down[self.dn_idx_counter].pop()
    
    def _generate_forecast_bands(self, is_uptrend_primary, current_bar_index_num):
        """Simulates PineScript's 'forecast' method. Returns dict of bands if generated."""
        forecast_bands = {"upper": [], "mid": [], "lower": []}
        
        memory_to_use = self.forecast_memory_up if is_uptrend_primary else self.forecast_memory_down
        current_idx_counter = self.up_idx_counter if is_uptrend_primary else self.dn_idx_counter
        init_price_for_forecast = self.uptrend_init_price if is_uptrend_primary else self.downtrend_init_price

        if init_price_for_forecast is None:
            logger.debug("Cannot generate forecast: init price not set for current trend.")
            return None

        max_historical_trend_len = len(memory_to_use) # Number of different trend lengths recorded

        for x in range(self.forecast_length): # Project 'forecast_length' bars ahead
            # PineScript: for i = idx-1 to math.min(idx+fcast, max_horizon-1)
            # This means it looks at vectors from trends that were one bar shorter than current,
            # up to trends that were 'forecast_length' bars longer (or max recorded length).
            # This is complex to replicate exactly without the full historical state of all past trends.
            # Simplified approach: Use all available vectors in memory_to_use for percentile calculation.
            # A more accurate translation would require storing each historical trend's full vector sequence.
            
            # For this simplified version, let's assume we use the vectors stored at indices
            # around the current_idx_counter. This is a significant simplification.
            # The PineScript logic implies a more sophisticated lookup of past similar trend developments.
            
            # Let's try to get *some* data for percentiles.
            # We'll use all price deltas from all recorded trend lengths (indices) in the current direction's memory.
            all_deltas_for_percentile = []
            for trend_len_idx in memory_to_use:
                # We need to consider how far into *those* trends the current projection 'x' would be.
                # This is where the PineScript `get_vector = get_holder.id.get(i)` is crucial.
                # `i` iterates through different historical trend lengths.
                # `get_vector.id` is the list of price deltas for that specific historical trend length.
                # This part is very hard to translate directly without a full Pine-like state machine.

                # Simplified: if memory_to_use[trend_len_idx] has enough data for the x-th step.
                # This is not what Pine does. Pine uses the *entire vector* from a past trend of length `i`
                # to calculate percentiles, not just one element.
                
                # A more direct (but still simplified) interpretation:
                # For each historical trend length 'i' (from current_idx_counter-1 up to a limit),
                # take its *entire vector* of price deltas.
                # This still doesn't quite match `get_vector.id.percentile_linear_interpolation`.
                # That function is called on a single vector (array of deltas for one specific past trend length).
                
                # Let's assume for now that the forecast is based on the *current* trend's memory vectors.
                # This is likely incorrect but a starting point.
                # The PineScript `get_holder.id.get(i)` suggests it's looking up vectors from *other* trend developments.
                
                # For now, this forecast part will be highly conceptual / placeholder
                # as replicating the exact PineScript state and lookup is very complex.
                pass # Placeholder for complex forecasting logic

            # If we had `all_deltas_for_percentile_at_step_x`
            # upper_val = init_price_for_forecast + percentile_linear_interpolation(all_deltas_for_percentile_at_step_x, self.forecast_top_percentile)
            # mid_val   = init_price_for_forecast + percentile_linear_interpolation(all_deltas_for_percentile_at_step_x, self.forecast_mid_percentile)
            # lower_val = init_price_for_forecast + percentile_linear_interpolation(all_deltas_for_percentile_at_step_x, self.forecast_bottom_percentile)
            
            # Placeholder forecast values
            offset = (x / self.forecast_length) * 0.01 * init_price_for_forecast # Simple linear projection
            forecast_bands["upper"].append({"time": current_bar_index_num + x, "value": init_price_for_forecast + offset * 2})
            forecast_bands["mid"].append({"time": current_bar_index_num + x, "value": init_price_for_forecast + offset})
            forecast_bands["lower"].append({"time": current_bar_index_num + x, "value": init_price_for_forecast - offset * 0.5})
            
        return forecast_bands


    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        logger.info(f"Running backtest for {self.name} on {self.symbol}...")
        if 'close' not in historical_df.columns:
            raise ValueError("Historical data must contain 'close' prices.")

        # 1. Prepare Data & Indicators
        # Primary timeframe MACD
        primary_macd_df = self._calculate_macd(historical_df, self.fast_len, self.slow_len, self.signal_len)
        df = historical_df.join(primary_macd_df)

        # HTF MACD
        if htf_historical_df is None or htf_historical_df.empty:
            logger.warning("Higher timeframe data not provided for backtest. HTF trend filter will be disabled.")
            df['htf_uptrend'] = True # Default to allow all trades if no HTF data
            df['htf_downtrend'] = True
        else:
            htf_macd_df = self._calculate_macd(htf_historical_df, self.fast_len, self.slow_len, self.signal_len)
            # Align HTF data to primary timeframe (forward fill)
            # This assumes htf_macd_df index is also datetime
            df = pd.merge_asof(df.sort_index(), htf_macd_df.sort_index().add_prefix('htf_'), 
                               left_index=True, right_index=True, direction='forward')
            
            if self.trend_determination == 'MACD':
                df['htf_uptrend'] = df['htf_macd'] > 0
                df['htf_downtrend'] = df['htf_macd'] < 0
            else: # 'MACD - Signal'
                df['htf_uptrend'] = df['htf_macd'] > df['htf_signal']
                df['htf_downtrend'] = df['htf_macd'] < df['htf_signal']
        
        df.fillna(method='ffill', inplace=True) # Fill NaNs from joins/MACD calculation start
        df.dropna(inplace=True) # Drop any remaining NaNs (usually at the beginning)
        if df.empty:
            logger.error("DataFrame is empty after indicator calculation and merging. Cannot backtest.")
            return {"pnl": 0, "trades": [], "message": "Not enough data for backtest."}

        trades = []
        position = 0 # 0: none, 1: long, -1: short
        entry_price = 0.0
        
        # Reset forecasting state for each backtest run
        self.forecast_memory_up = {}
        self.forecast_memory_down = {}
        self.up_idx_counter = 0
        self.dn_idx_counter = 0
        self.uptrend_init_price = None
        self.downtrend_init_price = None

        for i in range(1, len(df)): # Start from 1 to use df.iloc[i-1] for previous values
            current_close = df['close'].iloc[i]
            current_time_ts = df.index[i].timestamp() # For trade log

            # Primary trend determination (current timeframe)
            primary_uptrend_now = df['macd'].iloc[i] > (df['signal'].iloc[i] if self.trend_determination == 'MACD - Signal' else 0)
            primary_downtrend_now = df['macd'].iloc[i] < (df['signal'].iloc[i] if self.trend_determination == 'MACD - Signal' else 0)
            
            primary_uptrend_prev = df['macd'].iloc[i-1] > (df['signal'].iloc[i-1] if self.trend_determination == 'MACD - Signal' else 0)
            primary_downtrend_prev = df['macd'].iloc[i-1] < (df['signal'].iloc[i-1] if self.trend_determination == 'MACD - Signal' else 0)

            # MACD Cross detection (trigger)
            # ta.cross(macd, signal_or_zero)
            signal_or_zero_now = df['signal'].iloc[i] if self.trend_determination == 'MACD - Signal' else 0
            signal_or_zero_prev = df['signal'].iloc[i-1] if self.trend_determination == 'MACD - Signal' else 0
            
            crossed_above = df['macd'].iloc[i-1] <= signal_or_zero_prev and df['macd'].iloc[i] > signal_or_zero_now
            crossed_below = df['macd'].iloc[i-1] >= signal_or_zero_prev and df['macd'].iloc[i] < signal_or_zero_now
            trigger = crossed_above or crossed_below
            
            # Update forecast init prices
            if primary_uptrend_now and not primary_uptrend_prev: self.uptrend_init_price = current_close
            if primary_downtrend_now and not primary_downtrend_prev: self.downtrend_init_price = current_close
            
            # Populate forecast memory
            if primary_uptrend_now: self._populate_forecast_memory(True, current_close)
            if primary_downtrend_now: self._populate_forecast_memory(False, current_close)

            # Update idx counters
            self.up_idx_counter = 0 if not primary_uptrend_now else self.up_idx_counter + 1
            self.dn_idx_counter = 0 if not primary_downtrend_now else self.dn_idx_counter + 1

            # Generate forecast bands on trigger (conceptual, not used for trading in this version)
            # if trigger:
            #     forecast_bands = self._generate_forecast_bands(primary_uptrend_now, i)
            #     # These bands could be added to the results if needed for plotting

            # Entry Conditions
            long_condition = trigger and primary_uptrend_now and df['htf_uptrend'].iloc[i]
            short_condition = trigger and primary_downtrend_now and df['htf_downtrend'].iloc[i]

            # Exit current position if SL/TP hit or new opposing signal
            if position == 1: # Currently long
                sl = entry_price * (1 - self.stop_loss_percent)
                tp = entry_price * (1 + self.take_profit_percent)
                if (self.use_stop_loss and current_close <= sl) or \
                   (self.use_take_profit and current_close >= tp) or \
                   (short_condition and position != 0): # Close on opposing signal
                    reason = "SL" if current_close <= sl else ("TP" if current_close >= tp else "Opposing Signal")
                    trades.append({"type": "sell", "price": current_close, "time": current_time_ts, "entry_price": entry_price, "reason": reason})
                    position = 0
            elif position == -1: # Currently short
                sl = entry_price * (1 + self.stop_loss_percent)
                tp = entry_price * (1 - self.take_profit_percent)
                if (self.use_stop_loss and current_close >= sl) or \
                   (self.use_take_profit and current_close <= tp) or \
                   (long_condition and position != 0): # Close on opposing signal
                    reason = "SL" if current_close >= sl else ("TP" if current_close <= tp else "Opposing Signal")
                    trades.append({"type": "buy", "price": current_close, "time": current_time_ts, "entry_price": entry_price, "reason": reason})
                    position = 0
            
            # Enter new position
            if position == 0:
                if long_condition:
                    trades.append({"type": "buy", "price": current_close, "time": current_time_ts, "reason": "Long Entry"})
                    entry_price = current_close
                    position = 1
                elif short_condition:
                    trades.append({"type": "sell", "price": current_close, "time": current_time_ts, "reason": "Short Entry"})
                    entry_price = current_close
                    position = -1
        
        # Calculate P&L (simplified, assumes 1 unit per trade, no commission/slippage)
        total_pnl = 0
        processed_trades = []
        open_trade = None
        for t in trades:
            if t['type'] == 'buy':
                if open_trade and open_trade['type'] == 'short': # Closing short
                    pnl = (open_trade['price'] - t['price']) * self.lot_size
                    total_pnl += pnl
                    processed_trades.append({"entry_time": open_trade['time'], "exit_time": t['time'], "type": "short", "entry_price": open_trade['price'], "exit_price": t['price'], "pnl": pnl, "size": self.lot_size, "reason": t['reason']})
                    open_trade = None
                elif not open_trade : # Opening long
                    open_trade = {'type': 'long', 'price': t['price'], 'time': t['time']}
            elif t['type'] == 'sell':
                if open_trade and open_trade['type'] == 'long': # Closing long
                    pnl = (t['price'] - open_trade['price']) * self.lot_size
                    total_pnl += pnl
                    processed_trades.append({"entry_time": open_trade['time'], "exit_time": t['time'], "type": "long", "entry_price": open_trade['price'], "exit_price": t['price'], "pnl": pnl, "size": self.lot_size, "reason": t['reason']})
                    open_trade = None
                elif not open_trade: # Opening short
                    open_trade = {'type': 'short', 'price': t['price'], 'time': t['time']}
        
        return {
            "pnl": total_pnl,
            "trades": processed_trades,
            "sharpe_ratio": 0.0, # Placeholder
            "max_drawdown": 0.0, # Placeholder
        }

    def execute_live_signal(self, db_session: Session, subscription_id: int, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        """
        Executes the strategy's logic based on new market data for a live subscription.
        Manages position state in the database.
        """
        logger.debug(f"[{self.name}-{self.symbol}] Executing live signal check for subscription {subscription_id}...")

        if not exchange_ccxt:
            logger.error(f"[{self.name}-{self.symbol}] Exchange instance not provided.")
            return

        # Fetch the current position from the database
        current_position_db = db_session.query(Position).filter(
            Position.subscription_id == subscription_id,
            Position.is_open == True
        ).first()

        current_position_type = current_position_db.side if current_position_db else None # "long", "short", or None
        entry_price = current_position_db.entry_price if current_position_db else 0.0
        position_size_asset = current_position_db.amount if current_position_db else 0.0

        # Fetch necessary data (primary timeframe and HTF)
        try:
            # Need enough data for MACD calculation (slow_len + signal_len) plus some buffer
            ohlcv_primary = exchange_ccxt.fetch_ohlcv(self.symbol, self.timeframe, limit=self.slow_len + self.signal_len + 50)
            ohlcv_htf = exchange_ccxt.fetch_ohlcv(self.symbol, self.htf, limit=self.slow_len + self.signal_len + 50)

            if not ohlcv_primary or not ohlcv_htf:
                logger.warning(f"[{self.name}-{self.symbol}] Insufficient data fetched for live signal.")
                return

            df_primary = pd.DataFrame(ohlcv_primary, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_primary['timestamp'] = pd.to_datetime(df_primary['timestamp'], unit='ms')
            df_primary.set_index('timestamp', inplace=True)

            df_htf = pd.DataFrame(ohlcv_htf, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_htf['timestamp'] = pd.to_datetime(df_htf['timestamp'], unit='ms')
            df_htf.set_index('timestamp', inplace=True)

        except Exception as e:
            logger.error(f"[{self.name}-{self.symbol}] Error fetching data for live signal: {e}")
            return

        # Calculate Indicators (similar to backtest)
        primary_macd_df = self._calculate_macd(df_primary, self.fast_len, self.slow_len, self.signal_len)
        df = df_primary.join(primary_macd_df)

        htf_macd_df = self._calculate_macd(df_htf, self.fast_len, self.slow_len, self.signal_len)
        df = pd.merge_asof(df.sort_index(), htf_macd_df.sort_index().add_prefix('htf_'),
                           left_index=True, right_index=True, direction='forward')
        df.fillna(method='ffill', inplace=True)
        df.dropna(inplace=True)
        if df.empty:
            logger.warning(f"[{self.name}-{self.symbol}] Not enough processed data for live signal.")
            return

        # Get latest data row
        latest_data = df.iloc[-1]
        prev_data = df.iloc[-2] if len(df) > 1 else latest_data # Handle case with only one row
        current_price = latest_data['close']

        # Determine trends and triggers (similar to backtest)
        htf_uptrend = latest_data['htf_macd'] > (latest_data['htf_signal'] if self.trend_determination == 'MACD - Signal' else 0)
        htf_downtrend = latest_data['htf_macd'] < (latest_data['htf_signal'] if self.trend_determination == 'MACD - Signal' else 0)

        primary_uptrend_now = latest_data['macd'] > (latest_data['signal'] if self.trend_determination == 'MACD - Signal' else 0)
        primary_downtrend_now = latest_data['macd'] < (latest_data['signal'] if self.trend_determination == 'MACD - Signal' else 0)

        signal_or_zero_now = latest_data['signal'] if self.trend_determination == 'MACD - Signal' else 0
        signal_or_zero_prev = prev_data['signal'] if self.trend_determination == 'MACD - Signal' else 0

        crossed_above = prev_data['macd'] <= signal_or_zero_prev and latest_data['macd'] > signal_or_zero_now
        crossed_below = prev_data['macd'] >= signal_or_zero_prev and latest_data['macd'] < signal_or_zero_now
        trigger = crossed_above or crossed_below

        long_condition = trigger and primary_uptrend_now and htf_uptrend
        short_condition = trigger and primary_downtrend_now and htf_downtrend

        logger.debug(f"[{self.name}-{self.symbol}] Live Signal: LongCond={long_condition}, ShortCond={short_condition}, HTFUptrend={htf_uptrend}, HTFDowntrend={htf_downtrend}, Current Position={current_position_type}")

        # Exit logic
        if current_position_type == "long":
            sl_price = entry_price * (1 - self.stop_loss_percent)
            tp_price = entry_price * (1 + self.take_profit_percent)
            bearish_crossover = prev_data['macd'] >= signal_or_zero_prev and latest_data['macd'] < signal_or_zero_now # Use prev/current for crossover check

            exit_reason = None
            if self.use_stop_loss and current_price <= sl_price:
                exit_reason = "SL"
            elif self.use_take_profit and current_price >= tp_price:
                exit_reason = "TP"
            elif bearish_crossover: # Close on opposing signal
                 exit_reason = "Opposing Signal"

            if exit_reason:
                logger.info(f"[{self.name}-{self.symbol}] Closing LONG position at {current_price}. Reason: {exit_reason}")
                try:
                    # Cancel any open SL/TP orders for this position (requires tracking order IDs in DB)
                    # TODO: Implement cancellation of open orders associated with this position
                    # Example: exchange_ccxt.cancel_order(order_id, self.symbol)

                    # Execute market sell order to close position
                    # Ensure quantity precision
                    close_qty = exchange_ccxt.amount_to_precision(self.symbol, position_size_asset)
                    order = exchange_ccxt.create_market_sell_order(self.symbol, close_qty, params={'reduceOnly': True})
                    logger.info(f"[{self.name}-{self.symbol}] Placed LONG exit (SELL) order: {order.get('id')}")

                    # Create Order entry in DB
                    new_order = Order(
                        subscription_id=subscription_id,
                        order_id=order.get('id'), # Exchange's order ID
                        symbol=self.symbol,
                        order_type=order.get('type'),
                        side=order.get('side'),
                        amount=order.get('amount'),
                        price=order.get('price'), # May be None for market orders
                        cost=order.get('cost'),
                        filled=order.get('filled', 0.0),
                        remaining=order.get('remaining', order.get('amount')),
                        status=order.get('status', 'open'),
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_order)

                    # Update position in DB (simplified - ideally based on order fill)
                    current_position_db.is_open = False
                    current_position_db.closed_at = datetime.datetime.utcnow()
                    current_position_db.close_price = current_price # Use current price as simplified close price
                    current_position_db.pnl = (current_price - entry_price) * position_size_asset # Simplified PnL
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] LONG position closed and updated in DB.")

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error closing LONG position: {e}")
                    # TODO: Handle error - maybe update position status to 'error' in DB

        elif current_position_type == "short":
            sl_price = entry_price * (1 + self.stop_loss_percent)
            tp_price = entry_price * (1 - self.take_profit_percent)
            bullish_crossover = prev_data['macd'] <= signal_or_zero_prev and latest_data['macd'] > signal_or_zero_now # Use prev/current for crossover check

            exit_reason = None
            if self.use_stop_loss and current_price >= sl_price:
                exit_reason = "SL"
            elif self.use_take_profit and current_price <= tp_price:
                exit_reason = "TP"
            elif bullish_crossover: # Close on opposing signal
                 exit_reason = "Opposing Signal"

            if exit_reason:
                logger.info(f"[{self.name}-{self.symbol}] Closing SHORT position at {current_price}. Reason: {exit_reason}")
                try:
                    # Cancel any open SL/TP orders for this position (requires tracking order IDs in DB)
                    # TODO: Implement cancellation of open orders associated with this position

                    # Execute market buy order to close position
                    # Ensure quantity precision
                    close_qty = exchange_ccxt.amount_to_precision(self.symbol, position_size_asset)
                    order = exchange_ccxt.create_market_buy_order(self.symbol, close_qty, params={'reduceOnly': True})
                    logger.info(f"[{self.name}-{self.symbol}] Placed SHORT exit (BUY) order: {order.get('id')}")

                    # Create Order entry in DB
                    new_order = Order(
                        subscription_id=subscription_id,
                        order_id=order.get('id'), # Exchange's order ID
                        symbol=self.symbol,
                        order_type=order.get('type'),
                        side=order.get('side'),
                        amount=order.get('amount'),
                        price=order.get('price'), # May be None for market orders
                        cost=order.get('cost'),
                        filled=order.get('filled', 0.0),
                        remaining=order.get('remaining', order.get('amount')),
                        status=order.get('status', 'open'),
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_order)

                    # Update position in DB (simplified - ideally based on order fill)
                    current_position_db.is_open = False
                    current_position_db.closed_at = datetime.datetime.utcnow()
                    current_position_db.close_price = current_price # Use current price as simplified close price
                    current_position_db.pnl = (entry_price - current_price) * position_size_asset # Simplified PnL
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] SHORT position closed and updated in DB.")

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error closing SHORT position: {e}")
                    # TODO: Handle error - maybe update position status to 'error' in DB

        # Entry logic
        if current_position_type is None:
            if long_condition:
                logger.info(f"[{self.name}-{self.symbol}] LONG entry signal at {current_price}. Size: {self.lot_size}")
                try:
                    # Ensure quantity precision
                    entry_qty = exchange_ccxt.amount_to_precision(self.symbol, self.lot_size)
                    if entry_qty <= 0:
                         logger.warning(f"[{self.name}-{self.symbol}] Calculated entry quantity is zero or negative ({entry_qty}). Skipping order placement.")
                         return

                    # Execute market buy order
                    order = exchange_ccxt.create_market_buy_order(self.symbol, entry_qty)
                    logger.info(f"[{self.name}-{self.symbol}] Placed LONG entry (BUY) order: {order.get('id')}")

                    # Assume order is filled immediately for simplicity
                    actual_filled_price = float(order.get('price', current_price)) # Use actual fill price if available
                    actual_filled_quantity = float(order.get('amount', entry_qty)) # Use actual fill quantity

                    # Create new position in DB
                    new_position = Position(
                        subscription_id=subscription_id,
                        symbol=self.symbol,
                        exchange_name=exchange_ccxt.id, # Store exchange ID
                        side="long",
                        amount=actual_filled_quantity,
                        entry_price=actual_filled_price,
                        is_open=True,
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_position)
                    db_session.commit()
                    db_session.refresh(new_position) # Get the generated ID
                    logger.info(f"[{self.name}-{self.symbol}] LONG position created in DB: ID {new_position.id}.")

                    # Place SL/TP orders
                    if self.use_stop_loss or self.use_take_profit:
                        try:
                            sl_price = actual_filled_price * (1 - self.stop_loss_percent)
                            tp_price = actual_filled_price * (1 + self.take_profit_percent)

                            # Place Stop Loss order
                            if self.use_stop_loss:
                                sl_order = exchange_ccxt.create_order(
                                    self.symbol,
                                    'stop_market', # Or 'stop' depending on exchange support
                                    'sell',
                                    actual_filled_quantity,
                                    None, # Price is not needed for stop_market
                                    params={
                                        'stopPrice': exchange_ccxt.price_to_precision(self.symbol, sl_price),
                                        'reduceOnly': True
                                    }
                                )
                                logger.info(f"[{self.name}-{self.symbol}] Placed LONG SL (SELL) order: {sl_order.get('id')}")
                                # Create Order entry for SL in DB
                                new_sl_order = Order(
                                    subscription_id=subscription_id,
                                    order_id=sl_order.get('id'),
                                    symbol=self.symbol,
                                    order_type=sl_order.get('type'),
                                    side=sl_order.get('side'),
                                    amount=sl_order.get('amount'),
                                    price=sl_order.get('price'),
                                    cost=sl_order.get('cost'),
                                    filled=sl_order.get('filled', 0.0),
                                    remaining=sl_order.get('remaining', sl_order.get('amount')),
                                    status=sl_order.get('status', 'open'),
                                    created_at=datetime.datetime.utcnow(),
                                    updated_at=datetime.datetime.utcnow()
                                )
                                db_session.add(new_sl_order)


                            # Place Take Profit order
                            if self.use_take_profit:
                                tp_order = exchange_ccxt.create_order(
                                    self.symbol,
                                    'limit', # Or 'take_profit_limit'/'take_profit' depending on exchange
                                    'sell',
                                    actual_filled_quantity,
                                    exchange_ccxt.price_to_precision(self.symbol, tp_price), # Limit price
                                    params={
                                        'takeProfitPrice': exchange_ccxt.price_to_precision(self.symbol, tp_price), # Trigger price
                                        'reduceOnly': True
                                    }
                                )
                                logger.info(f"[{self.name}-{self.symbol}] Placed LONG TP (SELL) order: {tp_order.get('id')}")
                                 # Create Order entry for TP in DB
                                new_tp_order = Order(
                                    subscription_id=subscription_id,
                                    order_id=tp_order.get('id'),
                                    symbol=self.symbol,
                                    order_type=tp_order.get('type'),
                                    side=tp_order.get('side'),
                                    amount=tp_order.get('amount'),
                                    price=tp_order.get('price'),
                                    cost=tp_order.get('cost'),
                                    filled=tp_order.get('filled', 0.0),
                                    remaining=tp_order.get('remaining', tp_order.get('amount')),
                                    status=tp_order.get('status', 'open'),
                                    created_at=datetime.datetime.utcnow(),
                                    updated_at=datetime.datetime.utcnow()
                                )
                                db_session.add(new_tp_order)

                            db_session.commit() # Commit SL/TP orders to DB

                        except Exception as e:
                            logger.error(f"[{self.name}-{self.symbol}] Error placing SL/TP orders for LONG position: {e}")
                            # TODO: Handle error - maybe update position/subscription status

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error opening LONG position: {e}")
                    # TODO: Handle error - maybe update subscription status to 'error'

            elif short_condition:
                logger.info(f"[{self.name}-{self.symbol}] SHORT entry signal at {current_price}. Size: {self.lot_size}")
                try:
                    # Ensure quantity precision
                    entry_qty = exchange_ccxt.amount_to_precision(self.symbol, self.lot_size)
                    if entry_qty <= 0:
                         logger.warning(f"[{self.name}-{self.symbol}] Calculated entry quantity is zero or negative ({entry_qty}). Skipping order placement.")
                         return

                    # Execute market sell order
                    order = exchange_ccxt.create_market_sell_order(self.symbol, entry_qty)
                    logger.info(f"[{self.name}-{self.symbol}] Placed SHORT entry (SELL) order: {order.get('id')}")

                    # Assume order is filled immediately for simplicity
                    actual_filled_price = float(order.get('price', current_price)) # Use actual fill price if available
                    actual_filled_quantity = float(order.get('amount', entry_qty)) # Use actual fill quantity

                    # Create new position in DB
                    new_position = Position(
                        subscription_id=subscription_id,
                        symbol=self.symbol,
                        exchange_name=exchange_ccxt.id, # Store exchange ID
                        side="short",
                        amount=actual_filled_quantity,
                        entry_price=actual_filled_price,
                        is_open=True,
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_position)
                    db_session.commit()
                    db_session.refresh(new_position) # Get the generated ID
                    logger.info(f"[{self.name}-{self.symbol}] SHORT position created in DB: ID {new_position.id}.")

                    # Place SL/TP orders
                    if self.use_stop_loss or self.use_take_profit:
                        try:
                            sl_price = actual_filled_price * (1 + self.stop_loss_percent)
                            tp_price = actual_filled_price * (1 - self.take_profit_percent)

                            # Place Stop Loss order
                            if self.use_stop_loss:
                                sl_order = exchange_ccxt.create_order(
                                    self.symbol,
                                    'stop_market', # Or 'stop' depending on exchange support
                                    'buy',
                                    actual_filled_quantity,
                                    None, # Price is not needed for stop_market
                                    params={
                                        'stopPrice': exchange_ccxt.price_to_precision(self.symbol, sl_price),
                                        'reduceOnly': True
                                    }
                                )
                                logger.info(f"[{self.name}-{self.symbol}] Placed SHORT SL (BUY) order: {sl_order.get('id')}")
                                # Create Order entry for SL in DB
                                new_sl_order = Order(
                                    subscription_id=subscription_id,
                                    order_id=sl_order.get('id'),
                                    symbol=self.symbol,
                                    order_type=sl_order.get('type'),
                                    side=sl_order.get('side'),
                                    amount=sl_order.get('amount'),
                                    price=sl_order.get('price'),
                                    cost=sl_order.get('cost'),
                                    filled=sl_order.get('filled', 0.0),
                                    remaining=sl_order.get('remaining', sl_order.get('amount')),
                                    status=sl_order.get('status', 'open'),
                                    created_at=datetime.datetime.utcnow(),
                                    updated_at=datetime.datetime.utcnow()
                                )
                                db_session.add(new_sl_order)

                            # Place Take Profit order
                            if self.use_take_profit:
                                tp_order = exchange_ccxt.create_order(
                                    self.symbol,
                                    'limit', # Or 'take_profit_limit'/'take_profit' depending on exchange
                                    'buy',
                                    actual_filled_quantity,
                                    exchange_ccxt.price_to_precision(self.symbol, tp_price), # Limit price
                                    params={
                                        'takeProfitPrice': exchange_ccxt.price_to_precision(self.symbol, tp_price), # Trigger price
                                        'reduceOnly': True
                                    }
                                )
                                logger.info(f"[{self.name}-{self.symbol}] Placed SHORT TP (BUY) order: {tp_order.get('id')}")
                                 # Create Order entry for TP in DB
                                new_tp_order = Order(
                                    subscription_id=subscription_id,
                                    order_id=tp_order.get('id'),
                                    symbol=self.symbol,
                                    order_type=tp_order.get('type'),
                                    side=tp_order.get('side'),
                                    amount=tp_order.get('amount'),
                                    price=tp_order.get('price'),
                                    cost=tp_order.get('cost'),
                                    filled=tp_order.get('filled', 0.0),
                                    remaining=tp_order.get('remaining', tp_order.get('amount')),
                                    status=tp_order.get('status', 'open'),
                                    created_at=datetime.datetime.utcnow(),
                                    updated_at=datetime.datetime.utcnow()
                                )
                                db_session.add(new_tp_order)

                            db_session.commit() # Commit SL/TP orders to DB

                        except Exception as e:
                            logger.error(f"[{self.name}-{self.symbol}] Error placing SL/TP orders for SHORT position: {e}")
                            # TODO: Handle error - maybe update position/subscription status

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error opening SHORT position: {e}")
                    # TODO: Handle error - maybe update subscription status to 'error'

        logger.debug(f"[{self.name}-{self.symbol}] Live signal check complete. Current Position (DB): {current_position_type}")
