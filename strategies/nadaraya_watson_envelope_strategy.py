import pandas as pd
import numpy as np
import logging
import time
import ta # For indicators
 
logger = logging.getLogger(__name__)

class NadarayaWatsonEnvelopeStrategy:
    def __init__(self, symbol: str, timeframe: str, capital: float = 10000, **custom_parameters):
        self.symbol = symbol
        self.timeframe = timeframe # Used for fetching data if needed, and for context
        self.capital = capital     # For position sizing in backtest/live

        defaults = {
            "h_bandwidth": 8.0,  # Bandwidth for Gaussian kernel
            "multiplier": 3.0,   # Multiplier for envelope width
            "tp_percent": 1.0,   # Take Profit percentage
            "sl_percent": 0.5,   # Stop Loss percentage
            "position_size_percent_capital": 10.0 # e.g., 10% of capital per trade
        }
        for key, value in defaults.items():
            setattr(self, key, custom_parameters.get(key, value))

        # Convert percentages to multipliers for easier calculation
        self.tp_multiplier = 1 + (self.tp_percent / 100.0)
        self.sl_multiplier_long = 1 - (self.sl_percent / 100.0)
        self.tp_multiplier_short = 1 - (self.tp_percent / 100.0)
        self.sl_multiplier_short = 1 + (self.sl_percent / 100.0)
        
        # Live trading state variables
        self.live_position_active = False
        self.live_position_side = None # "long" or "short"
        self.live_entry_price = 0.0
        self.live_tp_price = 0.0
        self.live_sl_price = 0.0
        self.live_position_size_asset = 0.0

        logger.info(f"NadarayaWatsonEnvelopeStrategy initialized for {self.symbol} with params: {custom_parameters}")

    @classmethod
    def get_parameters_definition(cls):
        return {
            "h_bandwidth": {"type": "float", "default": 8.0, "label": "Kernel Bandwidth (h)", "description": "Bandwidth for the Gaussian kernel smoothing."},
            "multiplier": {"type": "float", "default": 3.0, "label": "Envelope Multiplier", "description": "Multiplier for the Mean Absolute Error to define envelope width."},
            "tp_percent": {"type": "float", "default": 1.0, "label": "Take Profit (%)", "description": "Take profit percentage from entry price."},
            "sl_percent": {"type": "float", "default": 0.5, "label": "Stop Loss (%)", "description": "Stop loss percentage from entry price."},
            "position_size_percent_capital": {"type": "float", "default": 10.0, "label": "Position Size (% of Capital)", "description": "Percentage of available capital to use for each trade."}
        }

    def _gauss(self, x, h):
        return np.exp(-((x ** 2) / (2 * h ** 2)))

    def _calculate_nadaraya_watson_envelope(self, close_prices_series: pd.Series):
        data = close_prices_series.values
        n = len(data)
        if n == 0:
            return pd.Series(dtype='float64'), pd.Series(dtype='float64'), pd.Series(dtype='float64')

        y_hat = np.zeros(n)
        for i in range(n):
            weighted_sum = 0
            total_weight = 0
            for j in range(n):
                weight = self._gauss(i - j, self.h_bandwidth)
                weighted_sum += data[j] * weight
                total_weight += weight
            if total_weight == 0: # Avoid division by zero if all weights are zero (e.g. h_bandwidth is tiny)
                y_hat[i] = data[i]
            else:
                y_hat[i] = weighted_sum / total_weight
        
        mae_value = np.abs(data - y_hat).mean() * self.multiplier
        upper_band = y_hat + mae_value
        lower_band = y_hat - mae_value

        return pd.Series(y_hat, index=close_prices_series.index), \
               pd.Series(upper_band, index=close_prices_series.index), \
               pd.Series(lower_band, index=close_prices_series.index)

    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None):
        if historical_df.empty or 'Close' not in historical_df.columns:
            logger.warning("Historical data is empty or missing 'Close' column for backtesting.")
            return {"status": "error", "message": "Invalid historical data.", "trades": [], "performance": {}}

        close_prices = historical_df['Close']
        _, upper_band, lower_band = self._calculate_nadaraya_watson_envelope(close_prices)

        if upper_band.empty or lower_band.empty:
             logger.warning("Nadaraya Watson envelope calculation resulted in empty bands.")
             return {"status": "error", "message": "Envelope calculation failed.", "trades": [], "performance": {}}


        trades = []
        position = None  # None, "long", "short"
        entry_price = 0
        tp_level = 0
        sl_level = 0
        equity = self.capital
        initial_equity = self.capital
        trade_count = 0
        winning_trades = 0

        # Ensure bands have same index as historical_df for proper alignment
        upper_band = upper_band.reindex(historical_df.index)
        lower_band = lower_band.reindex(historical_df.index)

        for i in range(len(historical_df)):
            current_time = historical_df.index[i]
            current_price = close_prices.iloc[i]
            current_upper = upper_band.iloc[i]
            current_lower = lower_band.iloc[i]

            if pd.isna(current_upper) or pd.isna(current_lower): # Skip if bands are not available
                continue

            # Position Sizing
            position_size_usdt = self.capital * (self.position_size_percent_capital / 100.0)
            # For backtesting, assume asset_qty is position_size_usdt / current_price for simplicity
            # A more robust backtester would handle this more precisely.

            # Check TP/SL for existing position
            if position == "long":
                if current_price >= tp_level:
                    exit_price = tp_level
                    profit = (exit_price - entry_price) * (position_size_usdt / entry_price) # Approx profit
                    equity += profit
                    trades.append({"entry_time": entry_time, "entry_price": entry_price, "exit_time": current_time, "exit_price": exit_price, "side": "long", "status": "TP", "profit": profit})
                    position = None
                    trade_count += 1
                    winning_trades +=1
                elif current_price <= sl_level:
                    exit_price = sl_level
                    loss = (exit_price - entry_price) * (position_size_usdt / entry_price) # Approx loss
                    equity += loss
                    trades.append({"entry_time": entry_time, "entry_price": entry_price, "exit_time": current_time, "exit_price": exit_price, "side": "long", "status": "SL", "profit": loss})
                    position = None
                    trade_count += 1
            elif position == "short":
                if current_price <= tp_level:
                    exit_price = tp_level
                    profit = (entry_price - exit_price) * (position_size_usdt / entry_price) # Approx profit
                    equity += profit
                    trades.append({"entry_time": entry_time, "entry_price": entry_price, "exit_time": current_time, "exit_price": exit_price, "side": "short", "status": "TP", "profit": profit})
                    position = None
                    trade_count += 1
                    winning_trades += 1
                elif current_price >= sl_level:
                    exit_price = sl_level
                    loss = (entry_price - exit_price) * (position_size_usdt / entry_price) # Approx loss
                    equity += loss
                    trades.append({"entry_time": entry_time, "entry_price": entry_price, "exit_time": current_time, "exit_price": exit_price, "side": "short", "status": "SL", "profit": loss})
                    position = None
                    trade_count += 1
            
            # Check for new entry signals if no position
            if position is None:
                if current_price <= current_lower: # Buy signal
                    position = "long"
                    entry_price = current_price
                    entry_time = current_time
                    tp_level = entry_price * self.tp_multiplier
                    sl_level = entry_price * self.sl_multiplier_long
                    # trades.append({"entry_time": current_time, "entry_price": entry_price, "side": "long", "status": "OPEN"}) # Log open
                elif current_price >= current_upper: # Sell signal
                    position = "short"
                    entry_price = current_price
                    entry_time = current_time
                    tp_level = entry_price * self.tp_multiplier_short
                    sl_level = entry_price * self.sl_multiplier_short
                    # trades.append({"entry_time": current_time, "entry_price": entry_price, "side": "short", "status": "OPEN"}) # Log open
        
        # If position is still open at the end, close it at last price
        if position is not None:
            exit_price = close_prices.iloc[-1]
            profit_or_loss = 0
            if position == "long":
                profit_or_loss = (exit_price - entry_price) * (position_size_usdt / entry_price)
            else: # short
                profit_or_loss = (entry_price - exit_price) * (position_size_usdt / entry_price)
            equity += profit_or_loss
            trades.append({"entry_time": entry_time, "entry_price": entry_price, "exit_time": historical_df.index[-1], "exit_price": exit_price, "side": position, "status": "CLOSED_END", "profit": profit_or_loss})

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
        for attempt in range(retries):
            try:
                # Ensure amount and price are formatted to exchange precision
                if price is not None:
                    price = exchange_ccxt.price_to_precision(symbol, price)
                amount_asset = exchange_ccxt.amount_to_precision(symbol, amount_asset)

                # Check minimum order size (cost) - this is a common requirement
                if price: # For limit orders
                    cost = amount_asset * price
                else: # For market orders, fetch current price to estimate cost
                    ticker = exchange_ccxt.fetch_ticker(symbol)
                    cost = amount_asset * ticker['last']
                
                markets = exchange_ccxt.load_markets()
                min_cost = markets[symbol].get('limits', {}).get('cost', {}).get('min')
                if min_cost and cost < min_cost:
                    logger.warning(f"Order cost {cost} is below minimum {min_cost} for {symbol}. Skipping order.")
                    return None

                order = exchange_ccxt.create_order(symbol, order_type, side, amount_asset, price)
                logger.info(f"Order placed: {side} {amount_asset} {symbol} at {price if price else 'market'}. Order ID: {order.get('id') if order else 'N/A'}")
                return order
            except Exception as e:
                logger.error(f"Error placing order (attempt {attempt + 1}/{retries}) for {symbol}: {e}")
                if "cost" in str(e).lower() or "size" in str(e).lower(): # Specific error for min cost/size
                    logger.error(f"Minimum cost or size issue for {symbol}. Check exchange limits.")
                    return None # Don't retry if it's a clear limits issue
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to place order after {retries} attempts for {symbol}.")
                    return None
        return None

    def execute_live_signal(self, market_data_df: pd.DataFrame = None, exchange_ccxt=None):
        if not exchange_ccxt:
            logger.error("Exchange CCXT object not provided.")
            return {"status": "error", "message": "Exchange not initialized."}

        # Fetch recent candles to calculate envelope. Need enough for the kernel.
        # Let's say we need at least 3 * h_bandwidth candles.
        # The platform's LiveStrategyRunner should ideally provide sufficient recent data.
        # If market_data_df is provided and sufficient, use it. Otherwise, fetch.
        
        required_candles = int(self.h_bandwidth * 3) + 5 # A bit more for stability
        if market_data_df is None or len(market_data_df) < required_candles:
            try:
                logger.info(f"Fetching {required_candles} recent candles for {self.symbol} timeframe {self.timeframe} for live signal.")
                ohlcv = exchange_ccxt.fetch_ohlcv(self.symbol, self.timeframe, limit=required_candles)
                if not ohlcv:
                    logger.warning(f"No OHLCV data returned for {self.symbol}")
                    return {"status": "no_action", "message": "No OHLCV data."}
                
                market_data_df = pd.DataFrame(ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                market_data_df['timestamp'] = pd.to_datetime(market_data_df['timestamp'], unit='ms')
                market_data_df.set_index('timestamp', inplace=True)
            except Exception as e:
                logger.error(f"Error fetching live OHLCV data for {self.symbol}: {e}")
                return {"status": "error", "message": f"Data fetch error: {e}"}
        
        if market_data_df.empty or 'Close' not in market_data_df.columns or len(market_data_df) < int(self.h_bandwidth): # Need at least h candles
            logger.warning("Not enough data to calculate Nadaraya-Watson envelope for live signal.")
            return {"status": "no_action", "message": "Insufficient data for envelope."}

        close_prices = market_data_df['Close']
        _, upper_band, lower_band = self._calculate_nadaraya_watson_envelope(close_prices)

        if upper_band.empty or lower_band.empty:
             logger.warning("Nadaraya Watson envelope calculation resulted in empty bands for live signal.")
             return {"status": "no_action", "message": "Envelope calculation failed."}

        current_price = close_prices.iloc[-1]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        
        if pd.isna(current_price) or pd.isna(current_upper) or pd.isna(current_lower):
            logger.warning("NaN values in price or envelope bands for live signal.")
            return {"status": "no_action", "message": "NaN in price/bands."}

        logger.debug(f"Live Signal Check: Symbol={self.symbol}, Price={current_price}, Lower={current_lower}, Upper={current_upper}, Active={self.live_position_active}, Side={self.live_position_side}")

        # Position Sizing for live trade
        # Calculate available capital (e.g. USDT balance)
        # For now, using self.capital as total available, and sizing based on that.
        # A real system would fetch current USDT balance.
        usdt_balance = self.capital # Placeholder for actual balance query
        position_size_usdt = usdt_balance * (self.position_size_percent_capital / 100.0)
        asset_qty_to_trade = position_size_usdt / current_price
        
        # Manage existing live position (TP/SL)
        if self.live_position_active:
            if self.live_position_side == "long":
                if current_price >= self.live_tp_price:
                    logger.info(f"Live TP hit for LONG {self.symbol} at {current_price}. Entry: {self.live_entry_price}, TP: {self.live_tp_price}")
                    order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', self.live_position_size_asset)
                    if order: self.live_position_active = False
                    return {"status": "action", "signal": "tp_long_close", "price": current_price, "size": self.live_position_size_asset}
                elif current_price <= self.live_sl_price:
                    logger.info(f"Live SL hit for LONG {self.symbol} at {current_price}. Entry: {self.live_entry_price}, SL: {self.live_sl_price}")
                    order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', self.live_position_size_asset)
                    if order: self.live_position_active = False
                    return {"status": "action", "signal": "sl_long_close", "price": current_price, "size": self.live_position_size_asset}
            
            elif self.live_position_side == "short":
                if current_price <= self.live_tp_price:
                    logger.info(f"Live TP hit for SHORT {self.symbol} at {current_price}. Entry: {self.live_entry_price}, TP: {self.live_tp_price}")
                    order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'buy', self.live_position_size_asset)
                    if order: self.live_position_active = False
                    return {"status": "action", "signal": "tp_short_close", "price": current_price, "size": self.live_position_size_asset}
                elif current_price >= self.live_sl_price:
                    logger.info(f"Live SL hit for SHORT {self.symbol} at {current_price}. Entry: {self.live_entry_price}, SL: {self.live_sl_price}")
                    order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'buy', self.live_position_size_asset)
                    if order: self.live_position_active = False
                    return {"status": "action", "signal": "sl_short_close", "price": current_price, "size": self.live_position_size_asset}
            
        # Check for new entry signals if no active live position
        if not self.live_position_active:
            if current_price <= current_lower: # Buy signal
                logger.info(f"Live BUY signal for {self.symbol} at {current_price} (Lower band: {current_lower})")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'buy', asset_qty_to_trade)
                if order:
                    self.live_position_active = True
                    self.live_position_side = "long"
                    self.live_entry_price = current_price # Or actual fill price from order
                    self.live_tp_price = self.live_entry_price * self.tp_multiplier
                    self.live_sl_price = self.live_entry_price * self.sl_multiplier_long
                    self.live_position_size_asset = asset_qty_to_trade # Or actual filled quantity
                    return {"status": "action", "signal": "buy_open", "price": current_price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}
            
            elif current_price >= current_upper: # Sell signal
                logger.info(f"Live SELL signal for {self.symbol} at {current_price} (Upper band: {current_upper})")
                order = self._place_order_with_retry(exchange_ccxt, self.symbol, 'market', 'sell', asset_qty_to_trade)
                if order:
                    self.live_position_active = True
                    self.live_position_side = "short"
                    self.live_entry_price = current_price # Or actual fill price
                    self.live_tp_price = self.live_entry_price * self.tp_multiplier_short
                    self.live_sl_price = self.live_entry_price * self.sl_multiplier_short
                    self.live_position_size_asset = asset_qty_to_trade # Or actual filled quantity
                    return {"status": "action", "signal": "sell_open", "price": current_price, "size": asset_qty_to_trade, "tp": self.live_tp_price, "sl": self.live_sl_price}

        return {"status": "no_action", "message": "Monitoring. No new signals or TP/SL hit."}

if __name__ == '__main__':
    # Setup basic logging for the test
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Mock CCXT exchange for testing
    class MockExchange:
        def __init__(self, symbol_precisions={'BTC/USDT': {'amount': 8, 'price': 2, 'cost_min': 10}}):
            self.markets = {}
            for sym, prec in symbol_precisions.items():
                self.markets[sym] = {
                    'symbol': sym,
                    'precision': {'amount': prec['amount'], 'price': prec['price']},
                    'limits': {'cost': {'min': prec.get('cost_min', 1)}}
                }
            self.last_price = 100.0
            self.orders = []

        def fetch_ohlcv(self, symbol, timeframe, limit):
            # Generate some dummy OHLCV data
            data = []
            price = self.last_price
            for i in range(limit):
                ts = int(time.time() * 1000) - (limit - i) * (60000 if timeframe == '1m' else 900000) # ms
                o = price * np.random.uniform(0.995, 1.005)
                h = max(o, price) * np.random.uniform(1.0, 1.01)
                l = min(o, price) * np.random.uniform(0.99, 1.0)
                c = h * np.random.uniform(0.99,1.0) if np.random.rand() > 0.5 else l * np.random.uniform(1.0,1.01)
                price = c
                data.append([ts, o, h, l, c, np.random.uniform(1,100)])
            return data
        
        def fetch_ticker(self, symbol):
            return {'symbol': symbol, 'last': self.last_price}

        def amount_to_precision(self, symbol, amount):
            prec = self.markets[symbol]['precision']['amount']
            return round(amount, prec)

        def price_to_precision(self, symbol, price):
            prec = self.markets[symbol]['precision']['price']
            return round(price, prec)
        
        def load_markets(self): return self.markets

        def create_order(self, symbol, type, side, amount, price=None, params=None):
            order_id = len(self.orders) + 1
            # Simulate fill price for market orders
            fill_price = price if price else self.last_price * (1.0005 if side == 'buy' else 0.9995)
            fill_price = self.price_to_precision(symbol, fill_price)
            
            order = {'id': order_id, 'symbol': symbol, 'type': type, 'side': side, 
                     'amount': amount, 'price': fill_price, # Use fill_price as the price for market
                     'status': 'closed', 'filled': amount, 'average': fill_price}
            self.orders.append(order)
            logger.info(f"[MockExchange] Order Created & Filled: {order}")
            return order

    mock_exchange = MockExchange()
    
    params = {
        "h_bandwidth": 8.0,
        "multiplier": 3.0,
        "tp_percent": 1.0,
        "sl_percent": 0.5,
        "position_size_percent_capital": 20.0
    }
    strategy = NadarayaWatsonEnvelopeStrategy(symbol="BTC/USDT", timeframe="15m", capital=1000, **params)

    # --- Test Backtest ---
    print("\n--- Test: Backtest ---")
    # Generate dummy historical data for backtest
    timestamps = pd.to_datetime(np.arange(int(time.time()) - 100 * 900, int(time.time()), 900), unit='s')
    close_prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    close_prices = np.maximum(close_prices, 10) # Ensure positive prices
    dummy_hist_df = pd.DataFrame({'Close': close_prices}, index=timestamps)
    
    backtest_results = strategy.run_backtest(dummy_hist_df.copy())
    print(f"Backtest Performance: {backtest_results.get('performance')}")
    # print(f"Backtest Trades: {backtest_results.get('trades')}")


    # --- Test Live Signal ---
    print("\n--- Test: Live Signal ---")
    # Simulate some price movements for live signals
    # 1. Price drops below lower band (BUY)
    mock_exchange.last_price = 95 # Assume lower band is ~96
    # Need to generate a df for market_data_df that would produce this lower band
    live_df_buy = strategy._calculate_nadaraya_watson_envelope(pd.Series(np.linspace(105, 95, 30)))[0].to_frame(name="Close") # Dummy
    live_df_buy.index = pd.to_datetime(np.arange(int(time.time()) - 30 * 900, int(time.time()), 900), unit='s')


    print("\n--- Test: Live BUY Signal ---")
    # For a more realistic test, we need market_data_df that would actually trigger a signal
    # This requires knowing what the bands would be.
    # Let's generate data where the last point is clearly below a calculated lower band.
    test_closes = np.array([100, 101, 100, 102, 101, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79])
    test_closes = np.concatenate((np.linspace(100, 90, 20), np.array([85, 80, 78, 75, 70]))) # Sharp drop
    live_market_df = pd.DataFrame({'Close': test_closes})
    live_market_df.index = pd.to_datetime(pd.date_range(end=pd.Timestamp.now(), periods=len(test_closes), freq='15min'))
    
    mock_exchange.last_price = live_market_df['Close'].iloc[-1] # Update mock exchange's current price
    result = strategy.execute_live_signal(market_data_df=live_market_df.copy(), exchange_ccxt=mock_exchange)
    print(f"Live Execute (BUY attempt): {result}")
    print(f"Strategy live state: active={strategy.live_position_active}, side={strategy.live_position_side}, entry={strategy.live_entry_price}, tp={strategy.live_tp_price}, sl={strategy.live_sl_price}")

    if strategy.live_position_active and strategy.live_position_side == "long":
        # 2. Price hits TP for long
        print("\n--- Test: Live TP for LONG ---")
        mock_exchange.last_price = strategy.live_tp_price * 1.001 # Move price to hit TP
        # Update market_data_df to reflect this new price as the last point
        new_row = live_market_df.iloc[[-1]].copy()
        new_row.index = new_row.index + pd.Timedelta(minutes=15)
        new_row['Close'] = mock_exchange.last_price
        live_market_df_tp = pd.concat([live_market_df, new_row])

        result = strategy.execute_live_signal(market_data_df=live_market_df_tp.copy(), exchange_ccxt=mock_exchange)
        print(f"Live Execute (TP LONG): {result}")
        print(f"Strategy live state after TP: active={strategy.live_position_active}")

    # 3. Price rises above upper band (SELL)
    test_closes_sell = np.concatenate((np.linspace(80, 90, 20), np.array([95, 100, 102, 105, 110]))) # Sharp rise
    live_market_df_sell = pd.DataFrame({'Close': test_closes_sell})
    live_market_df_sell.index = pd.to_datetime(pd.date_range(end=pd.Timestamp.now(), periods=len(test_closes_sell), freq='15min'))
    
    mock_exchange.last_price = live_market_df_sell['Close'].iloc[-1]

    print("\n--- Test: Live SELL Signal ---")
    result = strategy.execute_live_signal(market_data_df=live_market_df_sell.copy(), exchange_ccxt=mock_exchange)
    print(f"Live Execute (SELL attempt): {result}")
    print(f"Strategy live state: active={strategy.live_position_active}, side={strategy.live_position_side}, entry={strategy.live_entry_price}, tp={strategy.live_tp_price}, sl={strategy.live_sl_price}")

    if strategy.live_position_active and strategy.live_position_side == "short":
        # 4. Price hits SL for short
        print("\n--- Test: Live SL for SHORT ---")
        mock_exchange.last_price = strategy.live_sl_price * 1.001 # Move price to hit SL
        new_row_sl = live_market_df_sell.iloc[[-1]].copy()
        new_row_sl.index = new_row_sl.index + pd.Timedelta(minutes=15)
        new_row_sl['Close'] = mock_exchange.last_price
        live_market_df_sl = pd.concat([live_market_df_sell, new_row_sl])
        
        result = strategy.execute_live_signal(market_data_df=live_market_df_sl.copy(), exchange_ccxt=mock_exchange)
        print(f"Live Execute (SL SHORT): {result}")
        print(f"Strategy live state after SL: active={strategy.live_position_active}")
