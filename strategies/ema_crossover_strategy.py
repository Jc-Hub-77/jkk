# trading_platform/strategies/ema_crossover_strategy.py
import pandas as pd
import ta # For EMA calculation
import logging
import datetime
import json
from backend.models import Position, UserStrategySubscription, Order
from sqlalchemy.orm import Session
from backend.models import Position, UserStrategySubscription

logger = logging.getLogger(__name__)

class EMACrossoverStrategy:
    def __init__(self, symbol: str, timeframe: str, short_ema_period: int = 10, long_ema_period: int = 20, 
                 capital: float = 10000, risk_per_trade_percent: float = 0.01, # risk_per_trade_percent as decimal e.g. 0.01 for 1%
                 stop_loss_percent: float = 0.02, take_profit_percent: float = 0.04): # SL/TP as decimals
        self.symbol = symbol
        self.timeframe = timeframe
        self.short_ema_period = int(short_ema_period)
        self.long_ema_period = int(long_ema_period)
        self.capital = float(capital) # Initial capital for backtest, or allocated capital for live
        self.risk_per_trade_percent = float(risk_per_trade_percent)
        self.stop_loss_percent = float(stop_loss_percent)
        self.take_profit_percent = float(take_profit_percent)
        
        self.name = f"EMA Crossover ({self.short_ema_period}/{self.long_ema_period})"
        self.description = f"A simple EMA crossover strategy using {self.short_ema_period}-period and {self.long_ema_period}-period EMAs."
        
        # Live trading state
        self.in_position = None # None, "LONG", "SHORT"
        self.entry_price = 0.0
        self.position_size_asset = 0.0 # Quantity of the asset
        
        self.price_precision = 8 # Default, should be updated from exchange
        self.quantity_precision = 8 # Default

        logger.info(f"Initialized {self.name} for {self.symbol} on {self.timeframe}")

    @classmethod # Changed to classmethod as it doesn't rely on instance state
    def get_parameters_definition(cls):
        """Returns a definition of the parameters this strategy accepts."""
        return {
            "short_ema_period": {"type": "int", "default": 10, "min": 2, "max": 100, "label": "Short EMA Period"},
            "long_ema_period": {"type": "int", "default": 20, "min": 5, "max": 200, "label": "Long EMA Period"},
            "capital": {"type": "float", "default": 10000, "min": 100, "label": "Initial Capital (for backtest sizing)"},
            "risk_per_trade_percent": {"type": "float", "default": 1.0, "min": 0.1, "max": 10.0, "step": 0.1, "label": "Risk per Trade (% of Capital)"},
            "stop_loss_percent": {"type": "float", "default": 2.0, "min": 0.1, "step": 0.1, "label": "Stop Loss % from Entry"},
            "take_profit_percent": {"type": "float", "default": 4.0, "min": 0.1, "step": 0.1, "label": "Take Profit % from Entry"}
        }

    def _calculate_emas(self, df: pd.DataFrame):
        if 'close' not in df.columns:
            logger.error("DataFrame must contain 'close' column for EMA calculation.")
            return df
        df[f'ema_short'] = ta.ema(df['close'], length=self.short_ema_period)
        df[f'ema_long'] = ta.ema(df['close'], length=self.long_ema_period)
        return df

    def run_backtest(self, historical_df: pd.DataFrame, htf_historical_df: pd.DataFrame = None): # Added htf_historical_df for signature consistency
        logger.info(f"Running backtest for {self.name} on {self.symbol} ({self.timeframe})...")
        if historical_df.empty or len(historical_df) < self.long_ema_period:
            logger.warning("Not enough historical data for backtest.")
            return {"pnl": 0, "trades": [], "message": "Not enough data."}

        df = self._calculate_emas(historical_df.copy())
        df.dropna(inplace=True) # Remove rows with NaN EMAs at the beginning
        if df.empty:
            logger.warning("DataFrame empty after EMA calculation and NaN drop.")
            return {"pnl": 0, "trades": [], "message": "Not enough data after EMA calculation."}

        trades_log = []
        current_position = None # None, "LONG", "SHORT"
        entry_price = 0.0
        position_size = 0.0 # In asset quantity
        balance = self.capital

        for i in range(1, len(df)): # Start from 1 to compare with previous bar
            prev_row = df.iloc[i-1]
            current_row = df.iloc[i]
            current_price = current_row['close']
            current_time_ts = current_row.name.timestamp() # Assuming index is DatetimeIndex

            # Exit conditions first
            if current_position == "LONG":
                sl_price = entry_price * (1 - self.stop_loss_percent)
                tp_price = entry_price * (1 + self.take_profit_percent)
                if current_price <= sl_price:
                    pnl = (sl_price - entry_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "long", "entry_price": entry_price, "exit_price": sl_price, "size": position_size, "pnl": pnl, "reason": "SL"})
                    current_position = None
                elif current_price >= tp_price:
                    pnl = (tp_price - entry_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "long", "entry_price": entry_price, "exit_price": tp_price, "size": position_size, "pnl": pnl, "reason": "TP"})
                    current_position = None
                elif prev_row['ema_short'] >= prev_row['ema_long'] and current_row['ema_short'] < current_row['ema_long']: # Bearish crossover
                    pnl = (current_price - entry_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "long", "entry_price": entry_price, "exit_price": current_price, "size": position_size, "pnl": pnl, "reason": "Crossover Exit"})
                    current_position = None
            
            elif current_position == "SHORT":
                sl_price = entry_price * (1 + self.stop_loss_percent)
                tp_price = entry_price * (1 - self.take_profit_percent)
                if current_price >= sl_price:
                    pnl = (entry_price - sl_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "short", "entry_price": entry_price, "exit_price": sl_price, "size": position_size, "pnl": pnl, "reason": "SL"})
                    current_position = None
                elif current_price <= tp_price:
                    pnl = (entry_price - tp_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "short", "entry_price": entry_price, "exit_price": tp_price, "size": position_size, "pnl": pnl, "reason": "TP"})
                    current_position = None
                elif prev_row['ema_short'] <= prev_row['ema_long'] and current_row['ema_short'] > current_row['ema_long']: # Bullish crossover
                    pnl = (entry_price - current_price) * position_size
                    balance += pnl
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": current_time_ts, "type": "short", "entry_price": entry_price, "exit_price": current_price, "size": position_size, "pnl": pnl, "reason": "Crossover Exit"})
                    current_position = None

            # Entry conditions
            if current_position is None:
                # Bullish crossover: short EMA crosses above long EMA
                if prev_row['ema_short'] <= prev_row['ema_long'] and current_row['ema_short'] > current_row['ema_long']:
                    entry_price = current_price
                    entry_time_ts = current_time_ts
                    # Simplified position sizing for backtest: risk % of current balance
                    amount_to_risk = balance * self.risk_per_trade_percent
                    sl_distance = entry_price * self.stop_loss_percent
                    if sl_distance == 0: continue # Avoid division by zero
                    position_size = amount_to_risk / sl_distance
                    
                    trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "long", "entry_price": entry_price, "exit_price": None, "size": position_size, "pnl": None, "reason": "Crossover Entry"})
                    current_position = "LONG"
                    logger.info(f"Backtest: LONG entry at {entry_price} on {current_row.name}")

                # Bearish crossover: short EMA crosses below long EMA
                elif prev_row['ema_short'] >= prev_row['ema_long'] and current_row['ema_short'] < current_row['ema_long']:
                    entry_price = current_price
                    entry_time_ts = current_time_ts
                    amount_to_risk = balance * self.risk_per_trade_percent
                    sl_distance = entry_price * self.stop_loss_percent
                    if sl_distance == 0: continue
                    position_size = amount_to_risk / sl_distance

                    trades_log.append({"entry_time": entry_time_ts, "exit_time": None, "type": "short", "entry_price": entry_price, "exit_price": None, "size": position_size, "pnl": None, "reason": "Crossover Entry"})
                    current_position = "SHORT"
                    logger.info(f"Backtest: SHORT entry at {entry_price} on {current_row.name}")
        
        final_pnl = balance - self.capital
        logger.info(f"Backtest complete. Final PnL: {final_pnl:.2f}")
        return {
            "pnl": final_pnl,
            "sharpe_ratio": 0.0, # Placeholder - requires daily/periodic returns
            "max_drawdown": 0.0, # Placeholder - requires equity curve
            "trades": [t for t in trades_log if t['exit_time'] is not None], # Only completed trades
            "parameters_used": self.get_parameters_definition() # Return defaults, actuals are instance vars
        }

    def _get_precisions_live(self, exchange_ccxt):
        if not hasattr(self, '_live_precisions_fetched_'):
            try:
                exchange_ccxt.load_markets()
                market = exchange_ccxt.market(self.symbol)
                self.price_precision = market['precision']['price']
                self.quantity_precision = market['precision']['amount']
                setattr(self, '_live_precisions_fetched_', True)
            except Exception as e:
                logger.error(f"[{self.name}-{self.symbol}] Error fetching live precisions: {e}")

    def execute_live_signal(self, db_session: Session, subscription_id: int, market_data_df: pd.DataFrame, exchange_ccxt):
        """
        Executes the strategy's logic based on new market data for a live subscription.
        Manages position state in the database.
        """
        logger.debug(f"[{self.name}-{self.symbol}] Executing live signal check for subscription {subscription_id}...")

        # Fetch the current position from the database
        current_position_db = db_session.query(Position).filter(
            Position.subscription_id == subscription_id,
            Position.is_open == True
        ).first()

        current_position_type = current_position_db.side if current_position_db else None # "long", "short", or None
        entry_price = current_position_db.entry_price if current_position_db else 0.0
        position_size_asset = current_position_db.amount if current_position_db else 0.0

        if market_data_df.empty or len(market_data_df) < self.long_ema_period:
            logger.warning(f"[{self.name}-{self.symbol}] Insufficient market data for live signal.")
            return

        self._get_precisions_live(exchange_ccxt) # Ensure precisions are loaded

        df = self._calculate_emas(market_data_df.copy())
        df.dropna(inplace=True)
        if len(df) < 2:
            logger.warning(f"[{self.name}-{self.symbol}] Not enough data after EMA calculation for signal.")
            return

        latest_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        current_price = latest_row['close']

        # Exit logic
        if current_position_type == "long":
            sl_price = entry_price * (1 - self.stop_loss_percent)
            tp_price = entry_price * (1 + self.take_profit_percent)
            bearish_crossover = prev_row['ema_short'] >= prev_row['ema_long'] and latest_row['ema_short'] < latest_row['ema_long']
            if current_price <= sl_price or current_price >= tp_price or bearish_crossover:
                reason = "SL" if current_price <= sl_price else ("TP" if current_price >= tp_price else "Crossover Exit")
                logger.info(f"[{self.name}-{self.symbol}] Closing LONG position at {current_price}. Reason: {reason}")
                try:
                    # Execute sell order
                    # order = exchange_ccxt.create_market_sell_order(self.symbol, position_size_asset, params={'reduceOnly': True})
                    order = exchange_ccxt.create_market_sell_order(self.symbol, position_size_asset, params={'reduceOnly': True})
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
                    current_position_db.pnl = (current_price - entry_price) * position_size_asset # Simplified PnL
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] LONG position closed and updated in DB.")

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error closing LONG position: {e}")
                    # TODO: Handle error - maybe update position status to 'error' in DB

        elif current_position_type == "short":
            sl_price = entry_price * (1 + self.stop_loss_percent)
            tp_price = entry_price * (1 - self.take_profit_percent)
            bullish_crossover = prev_row['ema_short'] <= prev_row['ema_long'] and latest_row['ema_short'] > latest_row['ema_long']
            if current_price >= sl_price or current_price <= tp_price or bullish_crossover:
                reason = "SL" if current_price >= sl_price else ("TP" if current_price <= tp_price else "Crossover Exit")
                logger.info(f"[{self.name}-{self.symbol}] Closing SHORT position at {current_price}. Reason: {reason}")
                try:
                    # Execute buy order
                    # order = exchange_ccxt.create_market_buy_order(self.symbol, position_size_asset, params={'reduceOnly': True})
                    order = exchange_ccxt.create_market_buy_order(self.symbol, position_size_asset, params={'reduceOnly': True})
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
                    current_position_db.pnl = (entry_price - current_price) * position_size_asset # Simplified PnL
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] SHORT position closed and updated in DB.")

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error closing SHORT position: {e}")
                    # TODO: Handle error - maybe update position status to 'error' in DB

        # Entry logic
        if current_position_type is None:
            bullish_crossover = prev_row['ema_short'] <= prev_row['ema_long'] and latest_row['ema_short'] > latest_row['ema_long']
            bearish_crossover = prev_row['ema_short'] >= prev_row['ema_long'] and latest_row['ema_short'] < latest_row['ema_long']

            if bullish_crossover:
                entry_price = current_price
                # Simplified sizing for live: use a fraction of self.capital (allocated capital)
                # A more robust live sizing would fetch current account balance.
                # For now, use the capital from strategy parameters (which comes from subscription)
                subscription = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == subscription_id).first()
                allocated_capital = json.loads(subscription.custom_parameters).get("capital", self.capital) # Use capital from subscription params

                amount_to_risk_usd = allocated_capital * self.risk_per_trade_percent
                sl_distance_usd = entry_price * self.stop_loss_percent
                if sl_distance_usd == 0: return # Avoid division by zero
                position_size_asset = exchange_ccxt.amount_to_precision(self.symbol, amount_to_risk_usd / sl_distance_usd)
                
                logger.info(f"[{self.name}-{self.symbol}] LONG entry signal at {entry_price}. Size: {position_size_asset}")
                try:
                    # Execute buy order
                    # order = exchange_ccxt.create_market_buy_order(self.symbol, position_size_asset)
                    order = exchange_ccxt.create_market_buy_order(self.symbol, position_size_asset)
                    logger.info(f"[{self.name}-{self.symbol}] Placed LONG entry (BUY) order: {order.get('id')}")

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

                    # Create new position in DB (simplified - ideally based on order fill)
                    new_position = Position(
                        subscription_id=subscription_id,
                        symbol=self.symbol,
                        exchange_name=exchange_ccxt.id, # Store exchange ID
                        side="long",
                        amount=position_size_asset,
                        entry_price=entry_price,
                        is_open=True,
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_position)
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] LONG position created in DB.")

                    # Place SL/TP orders
                    try:
                        sl_price = entry_price * (1 - self.stop_loss_percent)
                        tp_price = entry_price * (1 + self.take_profit_percent)

                        # Place Stop Loss order
                        sl_order = exchange_ccxt.create_order(
                            self.symbol,
                            'stop_market', # Or 'stop' depending on exchange support
                            'sell',
                            position_size_asset,
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
                        tp_order = exchange_ccxt.create_order(
                            self.symbol,
                            'limit', # Or 'take_profit_limit'/'take_profit' depending on exchange
                            'sell',
                            position_size_asset,
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
                        db_session.commit() # Commit orders to DB

                    except Exception as e:
                        logger.error(f"[{self.name}-{self.symbol}] Error placing SL/TP orders for LONG position: {e}")
                        # TODO: Handle error - maybe update position/subscription status

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error opening LONG position: {e}")
                    # TODO: Handle error - maybe update subscription status to 'error'

            elif bearish_crossover:
                entry_price = current_price
                subscription = db_session.query(UserStrategySubscription).filter(UserStrategySubscription.id == subscription_id).first()
                allocated_capital = json.loads(subscription.custom_parameters).get("capital", self.capital) # Use capital from subscription params

                amount_to_risk_usd = allocated_capital * self.risk_per_trade_percent
                sl_distance_usd = entry_price * self.stop_loss_percent
                if sl_distance_usd == 0: return # Avoid division by zero
                position_size_asset = exchange_ccxt.amount_to_precision(self.symbol, amount_to_risk_usd / sl_distance_usd)

                logger.info(f"[{self.name}-{self.symbol}] SHORT entry signal at {entry_price}. Size: {position_size_asset}")
                try:
                    # Execute sell order
                    # order = exchange_ccxt.create_market_sell_order(self.symbol, position_size_asset)
                    order = exchange_ccxt.create_market_sell_order(self.symbol, position_size_asset)
                    logger.info(f"[{self.name}-{self.symbol}] Placed SHORT entry (SELL) order: {order.get('id')}")

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

                    # Create new position in DB (simplified - ideally based on order fill)
                    new_position = Position(
                        subscription_id=subscription_id,
                        symbol=self.symbol,
                        exchange_name=exchange_ccxt.id, # Store exchange ID
                        side="short",
                        amount=position_size_asset,
                        entry_price=entry_price,
                        is_open=True,
                        created_at=datetime.datetime.utcnow(),
                        updated_at=datetime.datetime.utcnow()
                    )
                    db_session.add(new_position)
                    db_session.commit()
                    logger.info(f"[{self.name}-{self.symbol}] SHORT position created in DB.")

                    # Place SL/TP orders
                    try:
                        sl_price = entry_price * (1 + self.stop_loss_percent)
                        tp_price = entry_price * (1 - self.take_profit_percent)

                        # Place Stop Loss order
                        sl_order = exchange_ccxt.create_order(
                            self.symbol,
                            'stop_market', # Or 'stop' depending on exchange support
                            'buy',
                            position_size_asset,
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
                        tp_order = exchange_ccxt.create_order(
                            self.symbol,
                            'limit', # Or 'take_profit_limit'/'take_profit' depending on exchange
                            'buy',
                            position_size_asset,
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
                        db_session.commit() # Commit orders to DB

                    except Exception as e:
                        logger.error(f"[{self.name}-{self.symbol}] Error placing SL/TP orders for SHORT position: {e}")
                        # TODO: Handle error - maybe update position/subscription status

                except Exception as e:
                    logger.error(f"[{self.name}-{self.symbol}] Error opening SHORT position: {e}")
                    # TODO: Handle error - maybe update subscription status to 'error'
        
        logger.debug(f"[{self.name}-{self.symbol}] Live signal check complete. Current Position (DB): {current_position_type}")

if __name__ == "__main__":
    # Example of creating dummy data for backtest
    data_len = 200
    dummy_dates = pd.to_datetime([datetime.datetime(2023,1,1) + datetime.timedelta(hours=i) for i in range(data_len)])
    dummy_data = pd.DataFrame({
        'open': np.random.rand(data_len) * 100 + 1000,
        'high': np.random.rand(data_len) * 100 + 1050,
        'low': np.random.rand(data_len) * 100 + 950,
        'close': np.random.rand(data_len) * 100 + 1000,
        'volume': np.random.rand(data_len) * 10
    }, index=dummy_dates)
    dummy_data['high'] = dummy_data[['open', 'close']].max(axis=1) + np.random.rand(data_len)*10
    dummy_data['low'] = dummy_data[['open', 'close']].min(axis=1) - np.random.rand(data_len)*10


    strategy_params = {
        "symbol": "BTC/USDT", "timeframe": "1h",
        "short_ema_period": 10, "long_ema_period": 20,
        "capital": 10000, "risk_per_trade_percent": 0.01, # 1%
        "stop_loss_percent": 0.02, "take_profit_percent": 0.04 # 2% SL, 4% TP
    }
    strategy = EMACrossoverStrategy(**strategy_params)
    
    print(f"Strategy: {strategy.name}")
    print("Parameters Definition:", strategy.get_parameters_definition())
    
    results = strategy.run_backtest(dummy_data)
    print("\nBacktest Results:")
    print(f"  PnL: {results.get('pnl', 0):.2f}")
    print(f"  Trades: {len(results.get('trades', []))}")
    # for trade in results.get('trades', []):
    #     print(f"    - {trade}")
