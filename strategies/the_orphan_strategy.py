import pandas as pd
import numpy as np
import logging
import time

logger = logging.getLogger(__name__)

class TheOrphanStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 2000, **custom_parameters):
        self.symbol = symbol
        self.timeframe_str = timeframe
        self.capital = capital

        # Default parameters based on PineScript
        defaults = {
            "bb_length": 14,
            "bb_stdev": 2.1,
            "trend_period": 90,
            "vol_filter_length": 15,
            "vol_ma_length": 28,
            "sl_percent": 2.0,
            "tp_percent": 9.0,
            "trail_stop_activation_percent": 0.5, # Percentage gain to activate trailing stop
            "trail_offset_percent": 0.5,        # Percentage offset for trailing stop
            "position_size_percent_equity": 10.0 # Percentage of equity per trade
        }
        for key, value in defaults.items():
            setattr(self, key, custom_parameters.get(key, value))

        # Convert percentages to multipliers
        self.sl_multiplier_long = 1 - (self.sl_percent / 100.0)
        self.tp_multiplier_long = 1 + (self.tp_percent / 100.0)
        self.sl_multiplier_short = 1 + (self.sl_percent / 100.0)
        self.tp_multiplier_short = 1 - (self.tp_percent / 100.0)
        self.trail_stop_activation_multiplier = 1 + (self.trail_stop_activation_percent / 100.0)
        self.trail_offset_multiplier = 1 - (self.trail_offset_percent / 100.0) # For long

        # Live trading state
        self.live_position_active = False
        self.live_position_side = None # "long" or "short"
        self.live_entry_price = 0.0
        self.live_position_size_asset = 0.0
        self.live_tp_price = 0.0
        self.live_sl_price = 0.0
        self.live_trailing_stop_price = 0.0
        self.live_trailing_stop_activated = False

        logger.info(f"TheOrphanStrategy initialized for {self.symbol} with params: {custom_parameters}")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "bb_length": {"type": "int", "default": 14, "min": 1, "label": "BB Length"},
            "bb_stdev": {"type": "float", "default": 2.1, "min": 0.1, "label": "BB StdDev"},
            "trend_period": {"type": "int", "default": 90, "min": 1, "label": "Trend Filter Period (EMA)"},
            "vol_filter_length": {"type": "int", "default": 15, "min": 1, "label": "Volatility Filter Length (StdDev)"},
            "vol_ma_length": {"type": "int", "default": 28, "min": 1, "label": "Volatility Filter MA Length"},
            "sl_percent": {"type": "float", "default": 2.0, "min": 0.1, "label": "Stop Loss (%)"},
            "tp_percent": {"type": "float", "default": 9.0, "min": 0.1, "label": "Take Profit (%)"},
            "trail_stop_activation_percent": {"type": "float", "default": 0.5, "min": 0.0, "label": "Trailing Stop Activation (%)", "description": "Percentage gain from entry to activate trailing stop."},
            "trail_offset_percent": {"type": "float", "default": 0.5, "min": 0.1, "label": "Trailing Offset (%)", "description": "Percentage offset for the trailing stop price."},
            "position_size_percent_equity": {"type": "float", "default": 10.0, "min": 0.1, "max": 100.0, "label": "Position Size (% of Equity)"}
        }

    def _calculate_indicators(self, df: pd.DataFrame):
        if df.empty or 'Close' not in df.columns:
            return None, None, None, None, None, None

        # Bollinger Bands
        # Need enough data for BB length
        if len(df) < self.bb_length:
            logger.debug(f"Not enough data ({len(df)}) for BB calculation (requires {self.bb_length}).")
            return None, None, None, None, None, None

        rolling_mean = df['Close'].rolling(window=self.bb_length).mean()
        rolling_std = df['Close'].rolling(window=self.bb_length).std()
        upper_band = rolling_mean + (rolling_std * self.bb_stdev)
        lower_band = rolling_mean - (rolling_std * self.bb_stdev)

        # Trend Filter (EMA)
        # Need enough data for Trend period
        if len(df) < self.trend_period:
             logger.debug(f"Not enough data ({len(df)}) for Trend EMA calculation (requires {self.trend_period}).")
             return None, None, None, None, None, None

        ema_trend = df['Close'].ewm(span=self.trend_period, adjust=False).mean()

        # Volatility Filter
        # Need enough data for Volatility filter length and MA length
        required_vol_data = max(self.vol_filter_length, self.vol_ma_length)
        if len(df) < required_vol_data:
             logger.debug(f"Not enough data ({len(df)}) for Volatility Filter calculation (requires {required_vol_data}).")
             return None, None, None, None, None, None

        vol_stddev = df['Close'].rolling(window=self.vol_filter_length).std()
        volatility_filter = vol_stddev > vol_stddev.rolling(window=self.vol_ma_length).mean()

        return upper_band, lower_band, ema_trend, volatility_filter, rolling_mean, rolling_std # Also return BB mid/std for context

    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        if historical_df.empty or not all(col in historical_df.columns for col in ['Open', 'High', 'Low', 'Close']):
            return {"status": "error", "message": "Invalid historical data for backtest.", "trades": [], "performance": {}}

        # Ensure DataFrame index is DatetimeIndex
        if not isinstance(historical_df.index, pd.DatetimeIndex):
            historical_df.index = pd.to_datetime(historical_df.index)
        
        df = historical_df.copy()

        # Calculate indicators
        upper_band, lower_band, ema_trend, volatility_filter, _, _ = self._calculate_indicators(df)

        # Need enough data for all indicators to be calculated
        required_data_len = max(self.bb_length, self.trend_period, self.vol_filter_length + self.vol_ma_length - 1) # Approx
        if upper_band is None or len(df) < required_data_len:
             logger.warning(f"Not enough data ({len(df)}) to calculate all indicators for backtest (requires approx {required_data_len}).")
             return {"status": "error", "message": "Insufficient data for indicator calculation.", "trades": [], "performance": {}}

        # Add indicators to DataFrame for easier access
        df['upper'] = upper_band
        df['lower'] = lower_band
        df['ema_trend'] = ema_trend
        df['volatility_filter'] = volatility_filter

        # Add shifted columns for conditions (PineScript uses lookback like close[1], close[2])
        df['Close_prev1'] = df['Close'].shift(1)
        df['Close_prev2'] = df['Close'].shift(2)
        df['upper_prev1'] = df['upper'].shift(1)
        df['lower_prev1'] = df['lower'].shift(1)


        trades = []
        position = None  # None, "long", "short"
        entry_price_bt = 0
        tp_level_bt = 0
        sl_level_bt = 0
        trailing_stop_price_bt = 0
        trailing_stop_activated_bt = False

        equity = self.capital
        initial_equity = self.capital
        trade_count = 0
        winning_trades = 0

        # Start iteration after the longest lookback period for indicators + conditions
        start_idx = max(required_data_len, 2) # Need at least 2 for close[2]

        for i in range(start_idx, len(df)):
            current_time = df.index[i]
            current_price = df['Close'].iloc[i]
            
            # Ensure indicator values are available for current bar
            if pd.isna(df['upper'].iloc[i]) or pd.isna(df['lower'].iloc[i]) or pd.isna(df['ema_trend'].iloc[i]) or pd.isna(df['volatility_filter'].iloc[i]):
                continue

            # Get values for conditions (using .iloc[-1] on shifted series gives value at current index i)
            close_prev1 = df['Close_prev1'].iloc[i]
            close_prev2 = df['Close_prev2'].iloc[i]
            upper_curr = df['upper'].iloc[i]
            lower_curr = df['lower'].iloc[i]
            ema_trend_curr = df['ema_trend'].iloc[i]
            volatility_filter_curr = df['volatility_filter'].iloc[i]

            if pd.isna(close_prev1) or pd.isna(close_prev2):
                 continue # Not enough lookback for conditions

            # Position Sizing (based on percentage of current equity)
            position_size_usdt_bt = equity * (self.position_size_percent_equity / 100.0)
            # For backtesting, assume asset_qty is position_size_usdt / current_price for simplicity

            # Check Exit Conditions (TP, SL, Trailing Stop, BB Crossover)
            if position == "long":
                # Check Trailing Stop Activation
                if not trailing_stop_activated_bt and current_price >= entry_price_bt * self.trail_stop_activation_multiplier:
                    trailing_stop_activated_bt = True
                    trailing_stop_price_bt = current_price * self.trail_offset_multiplier # Initial trailing stop
                    logger.debug(f"Backtest: Trailing stop activated for LONG at {current_time}. Initial stop: {trailing_stop_price_bt}")

                # Update Trailing Stop Price (only if activated)
                if trailing_stop_activated_bt:
                    new_trailing_stop = current_price * self.trail_offset_multiplier
                    trailing_stop_price_bt = max(trailing_stop_price_bt, new_trailing_stop) # Trail upwards

                # Check Exit Triggers
                exit_price = None
                exit_status = None

                if current_price >= tp_level_bt:
                    exit_price = tp_level_bt
                    exit_status = "TP"
                elif current_price <= sl_level_bt:
                    exit_price = sl_level_bt
                    exit_status = "SL"
                elif trailing_stop_activated_bt and current_price <= trailing_stop_price_bt:
                    exit_price = trailing_stop_price_bt
                    exit_status = "Trail Stop"
                elif close_prev1 >= upper_curr and close_prev2 < upper_curr: # BB Crossover Exit (close[1] crosses over upper)
                     # Note: PineScript uses close[1] crossover upper for exit.
                     # This translates to close_prev1 >= upper_curr AND close_prev2 < upper_curr
                     exit_price = current_price # Exit at current close on signal bar
                     exit_status = "BB Exit"

                if exit_price is not None:
                    profit = (exit_price - entry_price_bt) * (position_size_usdt_bt / entry_price_bt)
                    equity += profit
                    trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": current_time, "exit_price": exit_price, "side": "long", "status": exit_status, "profit": profit})
                    position = None
                    trade_count += 1
                    if exit_status in ["TP", "Trail Stop", "BB Exit"]: winning_trades += 1 # Consider BB Exit as winning if profitable? PineScript doesn't specify. Let's count TP/Trail as wins.
                    # Reset trailing stop state
                    trailing_stop_activated_bt = False
                    trailing_stop_price_bt = 0

            elif position == "short":
                 # Check Trailing Stop Activation (for short, price must drop below entry)
                if not trailing_stop_activated_bt and current_price <= entry_price_bt * (1 - self.trail_stop_activation_percent / 100.0):
                    trailing_stop_activated_bt = True
                    trailing_stop_price_bt = current_price * (1 + self.trail_offset_percent / 100.0) # Initial trailing stop (above price)
                    logger.debug(f"Backtest: Trailing stop activated for SHORT at {current_time}. Initial stop: {trailing_stop_price_bt}")

                # Update Trailing Stop Price (only if activated)
                if trailing_stop_activated_bt:
                    new_trailing_stop = current_price * (1 + self.trail_offset_percent / 100.0)
                    trailing_stop_price_bt = min(trailing_stop_price_bt, new_trailing_stop) # Trail downwards

                # Check Exit Triggers
                exit_price = None
                exit_status = None

                if current_price <= tp_level_bt:
                    exit_price = tp_level_bt
                    exit_status = "TP"
                elif current_price >= sl_level_bt:
                    exit_price = sl_level_bt
                    exit_status = "SL"
                elif trailing_stop_activated_bt and current_price >= trailing_stop_price_bt:
                    exit_price = trailing_stop_price_bt
                    exit_status = "Trail Stop"
                elif close_prev1 <= lower_curr and close_prev2 > lower_curr: # BB Crossover Exit (close[1] crosses under lower)
                     exit_price = current_price # Exit at current close on signal bar
                     exit_status = "BB Exit"

                if exit_price is not None:
                    profit = (entry_price_bt - exit_price) * (position_size_usdt_bt / entry_price_bt)
                    equity += profit
                    trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": current_time, "exit_price": exit_price, "side": "short", "status": exit_status, "profit": profit})
                    position = None
                    trade_count += 1
                    if exit_status in ["TP", "Trail Stop", "BB Exit"]: winning_trades += 1 # Consider BB Exit as winning if profitable?
                    # Reset trailing stop state
                    trailing_stop_activated_bt = False
                    trailing_stop_price_bt = 0


            # Check Entry Conditions (only if no position)
            if position is None:
                # Buy: close crosses over lower band AND close > ema_trend AND volatility_filter
                buy_condition_bb = current_price >= lower_curr and close_prev1 < lower_curr # Simple crossover check
                buy_condition_trend = current_price > ema_trend_curr
                buy_condition_final = buy_condition_bb and buy_condition_trend and volatility_filter_curr

                # Sell: close crosses over upper band AND close < ema_trend AND volatility_filter
                sell_condition_bb = current_price >= upper_curr and close_prev1 < upper_curr # Simple crossover check
                sell_condition_trend = current_price < ema_trend_curr
                sell_condition_final = sell_condition_bb and sell_condition_trend and volatility_filter_curr

                if buy_condition_final:
                    position = "long"
                    entry_price_bt = current_price
                    entry_time_bt = current_time
                    tp_level_bt = entry_price_bt * self.tp_multiplier_long
                    sl_level_bt = entry_price_bt * self.sl_multiplier_long
                    trailing_stop_activated_bt = False # Reset for new trade
                    trailing_stop_price_bt = 0
                    logger.debug(f"Backtest: Long entry at {entry_price_bt} on {entry_time_bt}")

                elif sell_condition_final:
                    position = "short"
                    entry_price_bt = current_price
                    entry_time_bt = current_time
                    tp_level_bt = entry_price_bt * self.tp_multiplier_short
                    sl_level_bt = entry_price_bt * self.sl_multiplier_short
                    trailing_stop_activated_bt = False # Reset for new trade
                    trailing_stop_price_bt = 0
                    logger.debug(f"Backtest: Short entry at {entry_price_bt} on {entry_time_bt}")

        # Close any open position at the end of backtest data
        if position is not None:
            exit_price_bt = df['Close'].iloc[-1]
            profit_or_loss = (exit_price_bt - entry_price_bt) * (position_size_usdt_bt / entry_price_bt) if position == "long" else (entry_price_bt - exit_price_bt) * (position_size_usdt_bt / entry_price_bt)
            equity += profit_or_loss
            trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": df.index[-1], "exit_price": exit_price_bt, "side": position, "status": "CLOSED_END", "profit": profit_or_loss})
            trade_count += 1 # Count as a trade

        total_return_percent = ((equity - initial_equity) / initial_equity) * 100 if initial_equity > 0 else 0
        win_rate = (winning_trades / trade_count) * 100 if trade_count > 0 else 0

        performance = {
            "initial_equity": initial_equity,
            "final_equity": equity,
            "total_return_usd": equity - initial_equity,
            "total_return_percent": total_return_percent,
            "total_trades": trade_count,
            "winning_trades": winning_trades,
            "losing_trades": trade_count - winning_trades,
            "win_rate_percent": win_rate
        }
        logger.info(f"Backtest completed. Performance: {performance}")
        return {"status": "success", "trades": trades, "performance": performance, "parameters": self.__dict__}

    def _place_order_with_retry(self, exchange_ccxt, symbol, order_type, side, amount_asset, price=None, retries=3, delay=5):
        # (Re-use the robust _place_order_with_retry from other strategies)
        for attempt in range(retries):
            try:
                if price is not None: price = exchange_ccxt.price_to_precision(symbol, price)
                amount_asset = exchange_ccxt.amount_to_precision(symbol, amount_asset)
                
                markets = exchange_ccxt.load_markets() 
                min_cost = markets[symbol].get('limits', {}).get('cost', {}).get('min')
                
                cost_estimate = 0
                if order_type.lower() == 'market':
                    ticker = exchange_ccxt.fetch_ticker(symbol)
                    cost_estimate = amount_asset * ticker['last']
                elif price: 
                    cost_estimate = amount_asset * price
                
                if min_cost and cost_estimate < min_cost:
                    logger.warning(f"Order cost {cost_estimate} for {amount_asset} {symbol} is below minimum {min_cost}. Skipping.")
                    return None

                order = exchange_ccxt.create_order(symbol, order_type, side, amount_asset, price)
                logger.info(f"Order placed: {side} {amount_asset} {symbol} at {price if price else 'market'}. ID: {order.get('id') if order else 'N/A'}")
                return order
            except Exception as e:
                logger.error(f"Error placing order (attempt {attempt+1}/{retries}) for {symbol}: {e}")
                if any(err_msg in str(e).upper() for err_msg in ["MIN_NOTIONAL", "LOT_SIZE", "SIZE_TOO_SMALL", "BELOW_MIN_TRADE_VALUE"]):
                    logger.error(f"Order size/cost issue for {symbol}: {e}. No further retries.")
                    return None 
                if attempt < retries - 1: time.sleep(delay)
                else: 
                    logger.error(f"Failed to place order for {symbol} after {retries} attempts.")
                    return None
        return None


    def execute_live_signal(self, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        if not exchange_ccxt: return {"status": "error", "message": "Exchange not initialized."}

        # Determine data needs: Need enough data for all indicators and conditions.
        # Max lookback for indicators is trend_period (90) or vol_filter_length + vol_ma_length - 1 (15+28-1=42).
        # Max lookback for conditions is 2 bars (close[2]).
        # So, need max(90, 42) + 2 = 92 bars minimum. Fetch a bit more for safety.
        required_bars = max(self.bb_length, self.trend_period, self.vol_filter_length + self.vol_ma_length - 1) + 2
        num_bars_to_fetch = max(required_bars, 150) # Fetch at least 150 bars

        if market_data_df is None or len(market_data_df) < required_bars:
            try:
                logger.info(f"Fetching {num_bars_to_fetch} recent candles for {self.symbol} timeframe {self.timeframe_str} for live signal.")
                ohlcv = exchange_ccxt.fetch_ohlcv(self.symbol, self.timeframe_str, limit=num_bars_to_fetch)
                if not ohlcv or len(ohlcv) < required_bars: return {"status": "no_action", "message": "Insufficient live OHLCV data."}
                
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
            except Exception as e:
                return {"status": "error", "message": f"Live data fetch error: {e}"}
        else: # Use provided market_data_df
            df = market_data_df.copy()
            if not isinstance(df.index, pd.DatetimeIndex): df.index = pd.to_datetime(df.index)
            if df.index.tzinfo is not None: df = df.tz_convert('UTC') # Ensure UTC if timezone present

        # Calculate indicators on the fetched/provided data
        upper_band, lower_band, ema_trend, volatility_filter, _, _ = self._calculate_indicators(df)

        if upper_band is None or len(df) < required_bars:
             logger.warning(f"Not enough data ({len(df)}) to calculate all indicators for live signal (requires approx {required_bars}).")
             return {"status": "no_action", "message": "Insufficient data for indicator calculation."}

        # Add indicators and shifted data for the latest bar
        df['upper'] = upper_band
        df['lower'] = lower_band
        df['ema_trend'] = ema_trend
        df['volatility_filter'] = volatility_filter
        df['Close_prev1'] = df['Close'].shift(1)
        df['Close_prev2'] = df['Close'].shift(2)
        df['upper_prev1'] = df['upper'].shift(1)
        df['lower_prev1'] = df['lower'].shift(1)

        # Get values for the latest completed bar
        price = df['Close'].iloc[-1]
        close_prev1 = df['Close_prev1'].iloc[-1]
        close_prev2 = df['Close_prev2'].iloc[-1]
        upper_curr = df['upper'].iloc[-1]
        lower_curr = df['lower'].iloc[-1]
        ema_trend_curr = df['ema_trend'].iloc[-1]
        volatility_filter_curr = df['volatility_filter'].iloc[-1]

        if any(pd.isna(x) for x in [price, close_prev1, close_prev2, upper_curr, lower_curr, ema_trend_curr, volatility_filter_curr]):
             logger.warning("NaN values in indicators or price data for live signal.")
             return {"status": "no_action", "message": "NaN data for signal check."}

        # Position Sizing
        # Fetch available balance (e.g., USDT)
        # available_balance_usdt = exchange_ccxt.fetch_balance()['USDT']['free'] # Example
        available_balance_usdt = self.capital # Using initial capital as placeholder
        position_size_usdt = available_balance_usdt * (self.position_size_percent_equity / 100.0)
        asset_qty_to_trade = position_size_usdt / price

        # Manage existing live position (TP, SL, Trailing Stop, BB Crossover Exit)
        if self.live_position_active:
            side_map = {"long": "sell", "short": "buy"}
            
            # Check Trailing Stop Activation (Live)
            if self.live_position_side == "long" and not self.live_trailing_stop_activated and price >= self.live_entry_price * self.trail_stop_activation_multiplier:
                 self.live_trailing_stop_activated = True
                 self.live_trailing_stop_price = price * self.trail_offset_multiplier
                 logger.info(f"Live: Trailing stop activated for LONG. Initial stop: {self.live_trailing_stop_price}")
            elif self.live_position_side == "short" and not self.live_trailing_stop_activated and price <= self.live_entry_price * (1 - self.trail_stop_activation_percent / 100.0):
                 self.live_trailing_stop_activated = True
                 self.live_trailing_stop_price = price * (1 + self.trail_offset_percent / 100.0)
                 logger.info(f"Live: Trailing stop activated for SHORT. Initial stop: {self.live_trailing_stop_price}")

            # Update Trailing Stop Price (Live)
            if self.live_trailing_stop_activated:
                if self.live_position_side == "long":
                    self.live_trailing_stop_price = max(self.live_trailing_stop_price, price * self.trail_offset_multiplier)
                elif self.live_position_side == "short":
                    self.live_trailing_stop_price = min(self.live_trailing_stop_price, price * (1 + self.trail_offset_percent / 100.0))
                logger.debug(f"Live: Updated trailing stop for {self.live_position_side.upper()}: {self.live_trailing_stop_price}")


            # Check Exit Triggers (Live)
            exit_price = None
            exit_status = None

            if self.live_position_side == "long":
                if price >= self.live_tp_price: exit_status = "TP"
                elif price <= self.live_sl_price: exit_status = "SL"
                elif self.live_trailing_stop_activated and price <= self.live_trailing_stop_price: exit_status = "Trail Stop"
                elif close_prev1 >= upper_curr and close_prev2 < upper_curr: exit_status = "BB Exit" # close[1] crosses over upper
            
            elif self.live_position_side == "short":
                if price <= self.live_tp_price: exit_status = "TP"
                elif price >= self.live_sl_price: exit_status = "SL"
                elif self.live_trailing_stop_activated and price >= self.live_trailing_stop_price: exit_status = "Trail Stop"
                elif close_prev1 <= lower_curr and close_prev2 > lower_curr: exit_status = "BB Exit" # close[1] crosses under lower

            if exit_status:
                logger.info(f"Live {exit_status} signal for {self.live_position_side.upper()} {self.symbol} at {price}.")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', side_map[self.live_position_side], self.live_position_size_asset)
                if order:
                    self.live_position_active = False
                    # Reset live state variables for next trade
                    self.live_position_side = None
                    self.live_entry_price = 0.0
                    self.live_position_size_asset = 0.0
                    self.live_tp_price = 0.0
                    self.live_sl_price = 0.0
                    self.live_trailing_stop_price = 0.0
                    self.live_trailing_stop_activated = False
                    return {"status": "action", "signal": f"{exit_status.lower().replace(' ', '_')}_{self.live_position_side}_close", "price": price, "size": self.live_position_size_asset}
                else:
                    logger.warning(f"Failed to place exit order for {exit_status}.")
                    # Keep position active if exit order failed? Or mark for retry?
                    # For now, assume failure means position is still open.
                    return {"status": "no_action", "message": f"Failed to place {exit_status} order."}


        # Check Entry Conditions (Live - only if no position)
        if not self.live_position_active:
            # Buy: close crosses over lower band AND close > ema_trend AND volatility_filter
            buy_condition_bb = price >= lower_curr and close_prev1 < lower_curr
            buy_condition_trend = price > ema_trend_curr
            buy_condition_final = buy_condition_bb and buy_condition_trend and volatility_filter_curr

            # Sell: close crosses over upper band AND close < ema_trend AND volatility_filter
            sell_condition_bb = price >= upper_curr and close_prev1 < upper_curr # Note: PineScript uses crossover for sell on upper band
            sell_condition_trend = price < ema_trend_curr
            sell_condition_final = sell_condition_bb and sell_condition_trend and volatility_filter_curr

            if buy_condition_final:
                logger.info(f"Live BUY signal for {self.symbol} at {price}.")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'buy', asset_qty_to_trade)
                if order:
                    self.live_position_active = True
                    self.live_position_side = "long"
                    self.live_entry_price = price # Use actual fill price if available from order
                    self.live_position_size_asset = asset_qty_to_trade # Use actual filled qty
                    self.live_tp_price = self.live_entry_price * self.tp_multiplier_long
                    self.live_sl_price = self.live_entry_price * self.sl_multiplier_long
                    self.live_trailing_stop_activated = False
                    self.live_trailing_stop_price = 0.0
                    return {"status": "action", "signal": "buy_open", "price": price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}
                else:
                    logger.warning("Failed to place live BUY order.")
                    return {"status": "no_action", "message": "Buy order placement failed."}

            elif sell_condition_final:
                logger.info(f"Live SELL signal for {self.symbol} at {price}.")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', asset_qty_to_trade)
                if order:
                    self.live_position_active = True
                    self.live_position_side = "short"
                    self.live_entry_price = price # Use actual fill price
                    self.live_position_size_asset = asset_qty_to_trade # Use actual filled qty
                    self.live_tp_price = self.live_entry_price * self.tp_multiplier_short
                    self.live_sl_price = self.live_entry_price * self.sl_multiplier_short
                    self.live_trailing_stop_activated = False
                    self.live_trailing_stop_price = 0.0
                    return {"status": "action", "signal": "sell_open", "price": price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}
                else:
                    logger.warning("Failed to place live SELL order.")
                    return {"status": "no_action", "message": "Sell order placement failed."}

        return {"status": "no_action", "message": "Monitoring. No signals or exit conditions met."}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    class MockExchange: # Simplified Mock for testing
        def __init__(self, symbol_precisions={'BTC/USDT': {'amount': 8, 'price': 2, 'cost_min': 10}}):
            self.markets = {sym: {'symbol': sym, 'precision': {'amount': prec['amount'], 'price': prec['price']}, 'limits': {'cost': {'min': prec.get('cost_min',1)}}} for sym, prec in symbol_precisions.items()}
            self.current_prices = {"BTC/USDT": 50000}
            self.orders = []
        def fetch_ohlcv(self, symbol, timeframe, limit):
            data = []
            base_price = self.current_prices.get(symbol, 50000)
            for i in range(limit):
                ts = int(time.time() * 1000) - (limit - i) * (60000 if timeframe == '1m' else 180*60*1000) # 3h intervals
                o = base_price * (1 + np.random.uniform(-0.01, 0.01))
                h = max(o, base_price) * (1 + np.random.uniform(0, 0.01))
                l = min(o, base_price) * (1 - np.random.uniform(0, 0.01))
                c = l + (h - l) * np.random.rand()
                data.append([ts, o, h, l, c, np.random.uniform(1,100)])
            return data
        def fetch_ticker(self, symbol): return {'symbol': symbol, 'last': self.current_prices.get(symbol, 50000)}
        def amount_to_precision(self, symbol, amount): return round(amount, self.markets[symbol]['precision']['amount'])
        def price_to_precision(self, symbol, price): return round(price, self.markets[symbol]['precision']['price'])
        def load_markets(self): return self.markets
        def create_order(self, symbol, type, side, amount, price=None, params=None):
            fill_price = price if price else self.current_prices.get(symbol,50000) * (1.0001 if side=='buy' else 0.9999)
            order = {'id': len(self.orders)+1, 'symbol': symbol, 'type': type, 'side': side, 'amount': amount, 'price': fill_price, 'status': 'closed', 'filled': amount, 'average': fill_price}
            self.orders.append(order); logger.info(f"[Mock] Order: {order}"); return order

    mock_ex = MockExchange()
    orphan_params = {
        "bb_length": 20, "bb_stdev": 2.0, "trend_period": 100, 
        "vol_filter_length": 20, "vol_ma_length": 30,
        "sl_percent": 3.0, "tp_percent": 10.0,
        "trail_stop_activation_percent": 1.0, "trail_offset_percent": 0.75,
        "position_size_percent_equity": 15.0
    }
    strategy = TheOrphanStrategy(symbol="BTC/USDT", timeframe="3h", capital=5000, **orphan_params)

    # Test Backtest
    print("\n--- Test: The Orphan Backtest ---")
    # Generate dummy historical data for backtest (need enough for indicators)
    required_bars = max(orphan_params['bb_length'], orphan_params['trend_period'], orphan_params['vol_filter_length'] + orphan_params['vol_ma_length'] - 1) + 2
    timestamps = pd.to_datetime(np.arange(int(time.time()) - (required_bars + 50) * 180 * 60, int(time.time()), 180 * 60), unit='s')
    close_prices = 50000 + np.cumsum(np.random.randn(len(timestamps)) * 100)
    close_prices = np.maximum(close_prices, 1000)
    dummy_hist_df = pd.DataFrame({'Open': close_prices.shift(1), 'High': close_prices + 50, 'Low': close_prices - 50, 'Close': close_prices, 'Volume': np.random.uniform(1,100, len(timestamps))})
    dummy_hist_df.iloc[0, dummy_hist_df.columns.get_loc('Open')] = dummy_hist_df.iloc[0, dummy_hist_df.columns.get_loc('Close')] # Fix first Open
    dummy_hist_df.index = timestamps
    
    backtest_results = strategy.run_backtest(dummy_hist_df.copy())
    print(f"Backtest Performance: {backtest_results.get('performance')}")
    # for trade in backtest_results.get('trades', []): print(trade)

    # Test Live Signal
    print("\n--- Test: The Orphan Live Signal ---")
    # Use the end of the generated historical data for live test
    mock_ex.current_prices["BTC/USDT"] = dummy_hist_df['Close'].iloc[-1]
    live_signal_res = strategy.execute_live_signal(market_data_df=dummy_hist_df.copy(), exchange_ccxt=mock_ex)
    print(f"Live Signal: {live_signal_res}")
    print(f"Strategy live state: active={strategy.live_position_active}, side={strategy.live_position_side}, entry={strategy.live_entry_price}, tp={strategy.live_tp_price}, sl={strategy.live_sl_price}, trail_active={strategy.live_trailing_stop_activated}, trail_price={strategy.live_trailing_stop_price}")
