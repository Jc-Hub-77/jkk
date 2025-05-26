import pandas as pd
import numpy as np
import logging
import time
import datetime
import pytz

logger = logging.getLogger(__name__)

class ORBStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, **custom_parameters):
        self.symbol = symbol
        self.timeframe_str = timeframe 
        self.capital = capital

        defaults = {
            "orb_hour": 9,
            "orb_minute": 15,
            "orb_timezone": "America/New_York",
            "tp_percent": 1.0,
            "sl_percent": 0.5,
            "position_size_percent_capital": 10.0,
            "lookback_bars_for_orb": 1 
        }
        for key, value in defaults.items():
            setattr(self, key, custom_parameters.get(key, value))

        try:
            self.pytz_orb_timezone = pytz.timezone(self.orb_timezone)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown ORB timezone '{self.orb_timezone}', defaulting to UTC.")
            self.pytz_orb_timezone = pytz.utc
            self.orb_timezone = "UTC"

        self.tp_multiplier = 1 + (self.tp_percent / 100.0)
        self.sl_multiplier_long = 1 - (self.sl_percent / 100.0)
        self.tp_multiplier_short = 1 - (self.tp_percent / 100.0)
        self.sl_multiplier_short = 1 + (self.sl_percent / 100.0)

        self.opening_range_high = None
        self.opening_range_low = None
        self.opening_range_set_for_date = None 

        self.live_position_active = False
        self.live_position_side = None
        self.live_entry_price = 0.0
        self.live_tp_price = 0.0
        self.live_sl_price = 0.0
        self.live_position_size_asset = 0.0
        
        logger.info(f"ORBStrategy initialized for {self.symbol} with params: {custom_parameters}, ORB Timezone: {self.orb_timezone}")

    @classmethod
    def get_parameters_definition(cls):
        common_timezones = [
            "UTC", "US/Eastern", "US/Central", "US/Pacific", "Europe/London", 
            "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney",
            "America/New_York", "America/Los_Angeles", "America/Chicago", "America/Phoenix", "America/Toronto",
            "America/Vancouver", "America/Argentina/Buenos_Aires", "America/El_Salvador", "America/Sao_Paulo", "America/Bogota",
            "Europe/Moscow", "Europe/Athens", "Europe/Madrid", "Europe/Paris", "Europe/Warsaw",
            "Australia/Brisbane", "Australia/Adelaide", "Asia/Almaty", "Asia/Ashkhabad", "Asia/Taipei", 
            "Asia/Singapore", "Asia/Shanghai", "Asia/Seoul", "Asia/Tehran", "Asia/Dubai", "Asia/Hong_Kong", "Asia/Bangkok",
            "Pacific/Auckland", "Pacific/Honolulu"
        ]
        return {
            "orb_hour": {"type": "int", "default": 9, "min": 0, "max": 23, "label": "ORB Hour", "description": "Hour to set the Opening Range (in selected timezone)."},
            "orb_minute": {"type": "int", "default": 15, "min": 0, "max": 59, "label": "ORB Minute", "description": "Minute to set the Opening Range."},
            "orb_timezone": {"type": "select", "default": "America/New_York", "options": common_timezones, "label": "ORB Timezone", "description": "Timezone for ORB calculation."},
            "lookback_bars_for_orb": {"type": "int", "default": 1, "min":1, "max":10, "label": "ORB Lookback Bars", "description": "Number of bars from ORB time to define the range (e.g., 1 for the single bar at H:M)."},
            "tp_percent": {"type": "float", "default": 1.0, "label": "Take Profit (%)"},
            "sl_percent": {"type": "float", "default": 0.5, "label": "Stop Loss (%)"},
            "position_size_percent_capital": {"type": "float", "default": 10.0, "label": "Position Size (% of Capital)"}
        }

    def _update_orb_range(self, df_in_orb_tz: pd.DataFrame, current_bar_dt_orb_tz: datetime.datetime):
        """
        Sets or confirms the ORB range for the current_bar_dt_orb_tz's date.
        df_in_orb_tz should have its index in the ORB timezone.
        """
        current_date_in_orb_tz = current_bar_dt_orb_tz.date()

        if self.opening_range_set_for_date == current_date_in_orb_tz:
            return # ORB already set for this date

        self.opening_range_high = None
        self.opening_range_low = None
        self.opening_range_set_for_date = current_date_in_orb_tz # Mark attempt for this date

        orb_target_datetime = datetime.datetime.combine(current_date_in_orb_tz, datetime.time(self.orb_hour, self.orb_minute), tzinfo=self.pytz_orb_timezone)
        
        # Find bars that constitute the ORB. These are `lookback_bars_for_orb` ending at or just after `orb_target_datetime`.
        # We need to find the first bar whose *end time* is >= orb_target_datetime.
        # Assuming df_in_orb_tz is sorted by time.
        
        # Find the bar that includes or immediately follows the orb_target_datetime
        orb_defining_bar_end_time = None
        orb_slice_df = pd.DataFrame()

        # Iterate through the df to find the segment for ORB
        # We need to find the bar whose start_time <= orb_target_datetime < end_time (or first bar after)
        # For simplicity, we'll look for the bar whose index (start time) is closest to or at orb_target_datetime
        
        # Get data for the specific date to narrow down search
        day_data = df_in_orb_tz[df_in_orb_tz.index.date == current_date_in_orb_tz]
        if day_data.empty:
            logger.debug(f"No data for {current_date_in_orb_tz} in ORB timezone to set range.")
            return

        # Find the index of the bar at or immediately after the ORB target time
        # This assumes the index is the start of the bar.
        potential_orb_bars = day_data[day_data.index >= orb_target_datetime]
        if not potential_orb_bars.empty:
            orb_bar_end_index_in_day_data = potential_orb_bars.index[0]
            # Now get the actual index from the original df_in_orb_tz
            orb_bar_actual_idx_pos = df_in_orb_tz.index.get_loc(orb_bar_end_index_in_day_data)

            start_slice_idx = orb_bar_actual_idx_pos - (self.lookback_bars_for_orb - 1)
            if start_slice_idx >= 0:
                orb_slice_df = df_in_orb_tz.iloc[start_slice_idx : orb_bar_actual_idx_pos + 1]
                # Verify all bars in slice are on the same day
                if all(b_idx.date() == current_date_in_orb_tz for b_idx in orb_slice_df.index):
                    self.opening_range_high = orb_slice_df['High'].max()
                    self.opening_range_low = orb_slice_df['Low'].min()
                    logger.info(f"ORB Set for {current_date_in_orb_tz}: H={self.opening_range_high}, L={self.opening_range_low} from {len(orb_slice_df)} bars ending {orb_slice_df.index[-1]}")
                else:
                    logger.debug(f"ORB slice for {current_date_in_orb_tz} spanned multiple days. ORB not set.")
            else:
                logger.debug(f"Not enough preceding data to form ORB for {current_date_in_orb_tz} at {orb_target_datetime}")
        else:
            logger.debug(f"No bar found at or after ORB target time {orb_target_datetime} for {current_date_in_orb_tz}")


    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        if historical_df.empty or not all(col in historical_df.columns for col in ['Open', 'High', 'Low', 'Close']):
            return {"status": "error", "message": "Invalid historical data for ORB backtest.", "trades": [], "performance": {}}

        if not isinstance(historical_df.index, pd.DatetimeIndex): historical_df.index = pd.to_datetime(historical_df.index)
        if historical_df.index.tzinfo is None: historical_df.index = historical_df.index.tz_localize('UTC')
        
        df = historical_df.copy()
        df.index = df.index.tz_convert(self.pytz_orb_timezone)

        df['c1'] = df['Close'].shift(1)
        df['c2'] = df['Close'].shift(2)
        df['c3'] = df['Close'].shift(3) # For crossover check
        df['h_curr'] = df['High']
        df['h1'] = df['High'].shift(1)
        df['h2'] = df['High'].shift(2)
        df['l_curr'] = df['Low']
        df['l1'] = df['Low'].shift(1)
        df['l2'] = df['Low'].shift(2)

        trades, position, entry_price_bt, tp_level_bt, sl_level_bt = [], None, 0, 0, 0
        equity, initial_equity, trade_count, winning_trades = self.capital, self.capital, 0, 0
        
        # Reset ORB state for backtest run
        self.opening_range_high = None
        self.opening_range_low = None
        self.opening_range_set_for_date = None

        for i in range(3, len(df)): # Start from 3 due to c3 lookback
            current_bar_dt = df.index[i]
            self._update_orb_range(df.iloc[:i+1], current_bar_dt) # Pass df up to current bar

            if self.opening_range_high is None or self.opening_range_low is None:
                continue # ORB not set for this bar's day yet

            # Ensure ORB is for the current bar's date
            if self.opening_range_set_for_date != current_bar_dt.date():
                continue

            price = df['Close'].iloc[i]
            # PineScript conditions translated:
            # close[2] is df['c2'].iloc[i]
            # high[1] is df['h1'].iloc[i], high[2] is df['h2'].iloc[i], high is df['h_curr'].iloc[i]
            # low[1] is df['l1'].iloc[i], low[2] is df['l2'].iloc[i], low is df['l_curr'].iloc[i]
            # ta.crossover(close[2], s_high) -> c2 > s_high AND c3 <= s_high
            # ta.crossunder(close[2], s_low) -> c2 < s_low AND c3 >= s_low
            
            # Check TP/SL
            if position == "long":
                if price >= tp_level_bt or price <= sl_level_bt:
                    status = "TP" if price >= tp_level_bt else "SL"
                    exit_p = tp_level_bt if status == "TP" else sl_level_bt
                    profit = (exit_p - entry_price_bt) * (self.capital * (self.position_size_percent_capital / 100.0) / entry_price_bt)
                    equity += profit
                    trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": current_bar_dt.tz_convert('UTC'), "exit_price": exit_p, "side": "long", "status": status, "profit": profit})
                    position, winning_trades = None, winning_trades + (1 if status == "TP" else 0)
                    trade_count +=1
            elif position == "short":
                if price <= tp_level_bt or price >= sl_level_bt:
                    status = "TP" if price <= tp_level_bt else "SL"
                    exit_p = tp_level_bt if status == "TP" else sl_level_bt
                    profit = (entry_price_bt - exit_p) * (self.capital * (self.position_size_percent_capital / 100.0) / entry_price_bt)
                    equity += profit
                    trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": current_bar_dt.tz_convert('UTC'), "exit_price": exit_p, "side": "short", "status": status, "profit": profit})
                    position, winning_trades = None, winning_trades + (1 if status == "TP" else 0)
                    trade_count += 1
            
            # Check Entry
            if position is None:
                buy_cond = (df['c2'].iloc[i] > self.opening_range_high and df['c3'].iloc[i] <= self.opening_range_high) and \
                           (df['h1'].iloc[i] > df['h2'].iloc[i]) and (df['h_curr'].iloc[i] > df['h1'].iloc[i])
                
                sell_cond = (df['c2'].iloc[i] < self.opening_range_low and df['c3'].iloc[i] >= self.opening_range_low) and \
                            (df['l1'].iloc[i] < df['l2'].iloc[i]) and (df['l_curr'].iloc[i] < df['l1'].iloc[i])

                if buy_cond:
                    position, entry_price_bt, entry_time_bt = "long", price, current_bar_dt.tz_convert('UTC')
                    tp_level_bt, sl_level_bt = entry_price_bt * self.tp_multiplier, entry_price_bt * self.sl_multiplier_long
                elif sell_cond:
                    position, entry_price_bt, entry_time_bt = "short", price, current_bar_dt.tz_convert('UTC')
                    tp_level_bt, sl_level_bt = entry_price_bt * self.tp_multiplier_short, entry_price_bt * self.sl_multiplier_short
        
        if position is not None: # Close open EOD
            exit_p = df['Close'].iloc[-1]
            profit = (exit_p - entry_price_bt) * (self.capital * (self.position_size_percent_capital / 100.0) / entry_price_bt) if position == "long" else \
                     (entry_price_bt - exit_p) * (self.capital * (self.position_size_percent_capital / 100.0) / entry_price_bt)
            equity += profit
            trades.append({"entry_time": entry_time_bt, "entry_price": entry_price_bt, "exit_time": df.index[-1].tz_convert('UTC'), "exit_price": exit_p, "side": position, "status": "CLOSED_END", "profit": profit})

        performance = {
            "initial_equity": initial_equity, "final_equity": equity, "total_return_usd": equity - initial_equity,
            "total_return_percent": ((equity - initial_equity) / initial_equity) * 100 if initial_equity > 0 else 0,
            "total_trades": trade_count, "winning_trades": winning_trades, "losing_trades": trade_count - winning_trades,
            "win_rate_percent": (winning_trades / trade_count) * 100 if trade_count > 0 else 0
        }
        return {"status": "success", "trades": trades, "performance": performance, "parameters": self.__dict__}

    def _place_order_with_retry(self, exchange_ccxt, symbol, order_type, side, amount_asset, price=None, retries=3, delay=5):
        # (Copied from NadarayaWatson - ensure it's robust)
        for attempt in range(retries):
            try:
                if price is not None: price = exchange_ccxt.price_to_precision(symbol, price)
                amount_asset = exchange_ccxt.amount_to_precision(symbol, amount_asset)
                
                markets = exchange_ccxt.load_markets() # Load fresh markets info
                min_cost = markets[symbol].get('limits', {}).get('cost', {}).get('min')
                
                # Estimate cost
                cost_estimate = 0
                if order_type.lower() == 'market':
                    # For market orders, fetch ticker to estimate cost if price not given
                    # However, for market order, amount is base currency, so cost is amount * current_price
                    ticker = exchange_ccxt.fetch_ticker(symbol)
                    cost_estimate = amount_asset * ticker['last']
                elif price: # For limit orders
                    cost_estimate = amount_asset * price
                
                if min_cost and cost_estimate < min_cost:
                    logger.warning(f"Order cost {cost_estimate} for {amount_asset} {symbol} is below minimum {min_cost}. Skipping.")
                    return None

                order = exchange_ccxt.create_order(symbol, order_type, side, amount_asset, price)
                logger.info(f"Order placed: {side} {amount_asset} {symbol} at {price if price else 'market'}. ID: {order.get('id') if order else 'N/A'}")
                return order
            except Exception as e:
                logger.error(f"Error placing order (attempt {attempt+1}/{retries}) for {symbol}: {e}")
                # Check for common CCXT error messages related to size/cost
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

        num_bars_to_fetch = 200 # Fetch a good amount of data for ORB setting and signals
        if market_data_df is None or len(market_data_df) < 4: # Need at least 4 for c3 and other lookbacks
            try:
                ohlcv = exchange_ccxt.fetch_ohlcv(self.symbol, self.timeframe_str, limit=num_bars_to_fetch)
                if not ohlcv or len(ohlcv) < 4: return {"status": "no_action", "message": "Insufficient live OHLCV data."}
                
                df_utc = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df_utc['timestamp'] = pd.to_datetime(df_utc['timestamp'], unit='ms')
                df_utc.set_index('timestamp', inplace=True)
            except Exception as e:
                return {"status": "error", "message": f"Live data fetch error: {e}"}
        else: # Use provided market_data_df
            df_utc = market_data_df.copy()
            if not isinstance(df_utc.index, pd.DatetimeIndex): df_utc.index = pd.to_datetime(df_utc.index)
        
        if df_utc.index.tzinfo is None: df_utc = df_utc.tz_localize('UTC')
        df = df_utc.tz_convert(self.pytz_orb_timezone)

        df['c1'] = df['Close'].shift(1)
        df['c2'] = df['Close'].shift(2)
        df['c3'] = df['Close'].shift(3)
        df['h_curr'] = df['High']
        df['h1'] = df['High'].shift(1)
        df['h2'] = df['High'].shift(2)
        df['l_curr'] = df['Low']
        df['l1'] = df['Low'].shift(1)
        df['l2'] = df['Low'].shift(2)

        # Current bar is the last bar in the dataframe
        current_bar_dt_orb_tz = df.index[-1]
        self._update_orb_range(df, current_bar_dt_orb_tz)

        if self.opening_range_high is None or self.opening_range_low is None or \
           self.opening_range_set_for_date != current_bar_dt_orb_tz.date():
            return {"status": "no_action", "message": "ORB not set for current bar's date."}

        # Data for the latest (just completed) bar for signal generation
        # PineScript conditions are based on historical bars relative to the current forming bar.
        # When `execute_live_signal` is called, the last row of `df` is the most recently *completed* bar.
        price = df['Close'].iloc[-1]
        c2, c3 = df['c2'].iloc[-1], df['c3'].iloc[-1]
        h_curr, h1, h2 = df['h_curr'].iloc[-1], df['h1'].iloc[-1], df['h2'].iloc[-1]
        l_curr, l1, l2 = df['l_curr'].iloc[-1], df['l1'].iloc[-1], df['l2'].iloc[-1]

        if any(pd.isna(x) for x in [price, c2, c3, h_curr, h1, h2, l_curr, l1, l2]):
            return {"status": "no_action", "message": "NaN data for signal check."}

        # Position Sizing
        # Fetch available balance (e.g., USDT)
        # available_balance_usdt = exchange_ccxt.fetch_balance()['USDT']['free'] # Example
        available_balance_usdt = self.capital # Using initial capital as placeholder
        position_size_usdt = available_balance_usdt * (self.position_size_percent_capital / 100.0)
        asset_qty_to_trade = position_size_usdt / price
        
        # Manage existing position
        if self.live_position_active:
            side_map = {"long": "sell", "short": "buy"}
            tp_cond = (self.live_position_side == "long" and price >= self.live_tp_price) or \
                      (self.live_position_side == "short" and price <= self.live_tp_price)
            sl_cond = (self.live_position_side == "long" and price <= self.live_sl_price) or \
                      (self.live_position_side == "short" and price >= self.live_sl_price)

            if tp_cond or sl_cond:
                action = "TP" if tp_cond else "SL"
                logger.info(f"Live {action} hit for {self.live_position_side.upper()} {self.symbol} at {price}.")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', side_map[self.live_position_side], self.live_position_size_asset)
                if order: self.live_position_active = False
                return {"status": "action", "signal": f"{action.lower()}_{self.live_position_side}_close", "price": price, "size": self.live_position_size_asset}
        
        # Check Entry
        if not self.live_position_active:
            buy_cond = (c2 > self.opening_range_high and c3 <= self.opening_range_high) and (h1 > h2) and (h_curr > h1)
            sell_cond = (c2 < self.opening_range_low and c3 >= self.opening_range_low) and (l1 < l2) and (l_curr < l1)

            if buy_cond:
                logger.info(f"Live BUY signal for {self.symbol} at {price}. ORB H:{self.opening_range_high}")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'buy', asset_qty_to_trade)
                if order:
                    self.live_position_active, self.live_position_side, self.live_entry_price = True, "long", price # Use actual fill price if available from order
                    self.live_tp_price, self.live_sl_price = price * self.tp_multiplier, price * self.sl_multiplier_long
                    self.live_position_size_asset = asset_qty_to_trade # Use actual filled qty
                    return {"status": "action", "signal": "buy_open", "price": price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}
            elif sell_cond:
                logger.info(f"Live SELL signal for {self.symbol} at {price}. ORB L:{self.opening_range_low}")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', asset_qty_to_trade)
                if order:
                    self.live_position_active, self.live_position_side, self.live_entry_price = True, "short", price
                    self.live_tp_price, self.live_sl_price = price * self.tp_multiplier_short, price * self.sl_multiplier_short
                    self.live_position_size_asset = asset_qty_to_trade
                    return {"status": "action", "signal": "sell_open", "price": price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}
        
        return {"status": "no_action", "message": "Monitoring ORB. No signals."}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    class MockExchange: # Simplified Mock
        def __init__(self, symbol_precisions={'BTC/USDT': {'amount': 8, 'price': 2, 'cost_min': 10}}):
            self.markets = {sym: {'symbol': sym, 'precision': {'amount': prec['amount'], 'price': prec['price']}, 'limits': {'cost': {'min': prec.get('cost_min',1)}}} for sym, prec in symbol_precisions.items()}
            self.current_prices = {"BTC/USDT": 50000} # Mock current price
            self.orders = []
        def fetch_ohlcv(self, symbol, timeframe, limit):
            data = []
            base_price = self.current_prices.get(symbol, 50000)
            for i in range(limit):
                ts = int(time.time() * 1000) - (limit - i) * (900*1000) # 15m intervals
                o = base_price * (1 + np.random.uniform(-0.005, 0.005))
                h = o * (1 + np.random.uniform(0, 0.005))
                l = o * (1 - np.random.uniform(0, 0.005))
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
    orb_params = {"orb_hour": 10, "orb_minute": 0, "orb_timezone": "UTC", "tp_percent": 0.5, "sl_percent": 0.25, "lookback_bars_for_orb": 1}
    strategy = ORBStrategy(symbol="BTC/USDT", timeframe="15m", capital=10000, **orb_params)

    # Test Backtest
    print("\n--- Test: ORB Backtest ---")
    # Create more realistic test data for backtest
    start_time_dt = datetime.datetime.now(pytz.UTC) - datetime.timedelta(days=5)
    timestamps_utc = pd.to_datetime([start_time_dt + datetime.timedelta(minutes=15*i) for i in range(400)]) # ~4 days of 15m data
    
    # Simulate data that would create an ORB and then trigger it
    orb_h, orb_m = orb_params['orb_hour'], orb_params['orb_minute']
    orb_data = []
    base_val = 50000
    for ts_utc in timestamps_utc:
        ts_orb_tz = ts_utc.astimezone(strategy.pytz_orb_timezone)
        o,h,l,c = base_val, base_val, base_val, base_val
        if ts_orb_tz.hour == orb_h and ts_orb_tz.minute == orb_m: # ORB bar
            o,h,l,c = base_val, base_val + 50, base_val - 50, base_val + np.random.randint(-40,40)
        elif ts_orb_tz.hour == orb_h and ts_orb_tz.minute > orb_m and ts_orb_tz.minute < orb_m + 30 : # After ORB, potential breakout
             # Simulate breakout: c2 > ORB_H and c3 <= ORB_H
            if len(orb_data) > 3 and orb_data[-2][4] > (base_val+50) and orb_data[-3][4] <= (base_val+50): # c2 > H, c3 <= H
                 o,h,l,c = base_val+60, base_val+150, base_val+55, base_val+120 # Strong breakout bar
            else:
                 o,h,l,c = base_val+np.random.randint(-20,20), base_val+np.random.randint(0,30), base_val-np.random.randint(0,30), base_val+np.random.randint(-25,25)
        else: # Regular bar
            o,h,l,c = base_val+np.random.randint(-20,20), base_val+np.random.randint(0,30), base_val-np.random.randint(0,30), base_val+np.random.randint(-25,25)
        
        base_val = c # Next bar opens near prev close
        orb_data.append([ts_utc.timestamp()*1000, o,h,l,c, 100])

    hist_df_test = pd.DataFrame(orb_data, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    hist_df_test['timestamp'] = pd.to_datetime(hist_df_test['timestamp'], unit='ms')
    hist_df_test.set_index('timestamp', inplace=True)

    backtest_res = strategy.run_backtest(hist_df_test.copy())
    print(f"Backtest Perf: {backtest_res.get('performance')}")
    # for trade in backtest_res.get('trades', []): print(trade)


    # Test Live Signal
    print("\n--- Test: ORB Live Signal ---")
    # Use the end of the generated historical data for live test
    mock_ex.current_prices["BTC/USDT"] = hist_df_test['Close'].iloc[-1]
    live_signal_res = strategy.execute_live_signal(market_data_df=hist_df_test.copy(), exchange_ccxt=mock_ex)
    print(f"Live Signal: {live_signal_res}")
    print(f"Strategy live state: active={strategy.live_position_active}, side={strategy.live_position_side}, entry={strategy.live_entry_price}, tp={strategy.live_tp_price}, sl={strategy.live_sl_price}")
