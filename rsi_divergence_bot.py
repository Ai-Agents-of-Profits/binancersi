import ccxt
import os
import time
import schedule
import logging
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime
from colorama import Fore, Style
import colorama
import threading

# --- Local Modules ---
from state_manager_rsidiv import initialize_state, get_state, set_state, reset_state
from functions_rsidiv import fetch_candles, compute_indicators, detect_rsi_divergence

# --- Setup ---
colorama.init(autoreset=True)
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Configuration ---
SYMBOL = 'BERA/USDT:USDT'  # Bybit symbol format
TIMEFRAME = '1h'
ORDER_SIZE_USD = 1000  # Match your backtest
FETCH_LIMIT = 200
SCHEDULE_INTERVAL_SECONDS = 60
TRAILING_STOP_FAST_INTERVAL = 5  # seconds

# Strategy Parameters (Best Backtest)
RSI_LENGTH = 6
ATR_LENGTH = 6
ATR_MULTIPLIER = 1.1
PROFIT_TARGET_PCT = 0.01
STOP_LOSS_PCT = 0.008
SWING_WINDOW = 4

# --- Exchange Setup ---
logging.info("Connecting to Bybit...")
try:
    exchange_config = {
        'enableRateLimit': True,
        'apiKey': os.getenv('BYBIT_API_KEY'),
        'secret': os.getenv('BYBIT_API_SECRET'),
        'options': {
            'defaultType': 'linear',  # Bybit USDT Perpetual
            'timeout': 30000,
        }
    }
    # Proxy
    exchange_config['proxies'] = {
        'http': 'http://14a72d94fa368:6d4cde2dfa@181.214.172.24:12323/',
        'https': 'http://14a72d94fa368:6d4cde2dfa@181.214.172.24:12323/'
    }
    logging.info("Using custom proxy for connection")
    exchange = ccxt.bybit(exchange_config)
    exchange.load_markets()
    market = exchange.market(SYMBOL)
    AMOUNT_PRECISION = market['precision']['amount']
    PRICE_PRECISION = market['precision']['price']
    MIN_AMOUNT = market['limits']['amount']['min']
    MIN_NOTIONAL = market['limits']['cost']['min'] or 5  # fallback to 5 if not set
    logging.info(f"Connected to Bybit. Amount precision: {AMOUNT_PRECISION}, Price precision: {PRICE_PRECISION}, Min amount: {MIN_AMOUNT}, Min notional: {MIN_NOTIONAL}")
except Exception as e:
    logging.critical(f"Exchange setup failed: {e}", exc_info=True)
    exit()

def step_to_decimals(step):
    if step >= 1:
        return 0
    s = str(step)
    if '.' in s:
        return len(s.split('.')[1].rstrip('0'))
    return 0

# --- State ---
initialize_state()

def print_header():
    print(f"\n{Fore.CYAN}{'=' * 80}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}{'RSI DIVERGENCE STRATEGY BOT (Bybit)':^80}")
    print(f"{Fore.CYAN}{'-' * 80}")
    print(f"{Fore.GREEN}Symbol: {Fore.WHITE}{SYMBOL.replace('/', '')} | {Fore.GREEN}Timeframe: {Fore.WHITE}{TIMEFRAME} | {Fore.GREEN}Order Size: {Fore.WHITE}${ORDER_SIZE_USD} USD")
    print(f"{Fore.GREEN}RSI: {Fore.WHITE}{RSI_LENGTH} | ATR: {Fore.WHITE}{ATR_LENGTH} | ATR Mult: {Fore.WHITE}{ATR_MULTIPLIER} | PT: {Fore.WHITE}{PROFIT_TARGET_PCT*100:.2f}% | SL: {Fore.WHITE}{STOP_LOSS_PCT*100:.2f}% | SWING: {Fore.WHITE}{SWING_WINDOW}")
    print(f"{Fore.CYAN}{'=' * 80}\n{Style.RESET_ALL}")

print_header()

# --- Trailing Stop Helper ---
def update_trailing_stop(state, price, atr_val):
    is_long = state['position_side'] == 'long'
    atr_at_entry = state.get('atr_at_entry', atr_val)
    if is_long:
        if state['highest'] is None or price > state['highest']:
            state['highest'] = price
        trail_dist = max(ATR_MULTIPLIER * atr_at_entry, price * STOP_LOSS_PCT)
        new_trail = state['highest'] - trail_dist
        if state['trailing_stop_level'] is None or new_trail > state['trailing_stop_level']:
            state['trailing_stop_level'] = new_trail
    else:
        if state['lowest'] is None or price < state['lowest']:
            state['lowest'] = price
        trail_dist = max(ATR_MULTIPLIER * atr_at_entry, price * STOP_LOSS_PCT)
        new_trail = state['lowest'] + trail_dist
        if state['trailing_stop_level'] is None or new_trail < state['trailing_stop_level']:
            state['trailing_stop_level'] = new_trail
    return state

def trailing_stop_checker():
    while True:
        time.sleep(TRAILING_STOP_FAST_INTERVAL)
        state = get_state()
        if not state.get('active_trade', False) or state.get('closing', False):
            continue
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            price = ticker['last']
            atr_at_entry = state.get('atr_at_entry')
            if atr_at_entry is None:
                df = fetch_candles(exchange, SYMBOL, TIMEFRAME, 20)
                df = compute_indicators(df, rsi_length=RSI_LENGTH, atr_length=ATR_LENGTH)
                atr_at_entry = df['ATR'].iloc[-1]
            state = update_trailing_stop(state, price, atr_at_entry)
            is_long = state['position_side'] == 'long'
            if (is_long and price <= state['trailing_stop_level']) or (not is_long and price >= state['trailing_stop_level']):
                if not state.get('closing', False):
                    state['closing'] = True
                    set_state(state)
                    print(f"\n{Fore.RED}{Style.BRIGHT}FAST EXIT: Trailing stop hit! Closing {state['position_side']} position.{Style.RESET_ALL}")
                    logging.info(f"FAST EXIT: Trailing stop hit! Closing {state['position_side']} position.")
                    for oid in [state.get('sl_order_id'), state.get('tp_order_id')]:
                        if oid:
                            try:
                                exchange.cancel_order(oid, SYMBOL)
                                logging.info(f"Cancelled open order: {oid}")
                            except Exception as e:
                                logging.warning(f"Failed to cancel order {oid}: {e}")
                    positions = exchange.fetch_positions(symbols=[SYMBOL])
                    exch_pos = next(
                        (p for p in positions if p['symbol'] == SYMBOL or p['symbol'].replace(':USDT', '') == SYMBOL or SYMBOL in p['symbol']),
                        None
                    )
                    exch_size = abs(float(exch_pos['info'].get('positionAmt', '0'))) if exch_pos else 0
                    side = 'sell' if is_long else 'buy'
                    try:
                        params = {'reduceOnly': True}
                        if exch_size > 0:
                            order = exchange.create_market_order(SYMBOL, side, exch_size, params=params)
                            logging.info(f"Market close order placed: {order.get('id', 'N/A')}")
                        reset_state()
                        print(f"{Fore.MAGENTA}Position closed by trailing stop. State reset.{Style.RESET_ALL}")
                    except Exception as e:
                        logging.error(f"Market close FAILED: {e}")
                continue
            set_state(state)
        except Exception as e:
            logging.error(f"Error in trailing_stop_checker: {e}")

def bot_logic():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n{Fore.CYAN}[{now}] {Style.BRIGHT}Running RSI Divergence Cycle [{TIMEFRAME}] {Style.RESET_ALL}")
    logging.info(f"--- Running RSI Divergence Cycle [{TIMEFRAME}] ---")
    state = get_state()
    if state.get('closing', False):
        logging.info("Bot is currently closing a position. Skipping cycle.")
        return
    try:
        positions = exchange.fetch_positions(symbols=[SYMBOL])
        exch_pos = next(
            (p for p in positions if p['symbol'] == SYMBOL or p['symbol'].replace(':USDT', '') == SYMBOL or SYMBOL in p['symbol']),
            None
        )
        exch_size_str = exch_pos['info'].get('positionAmt', '0') if exch_pos else '0'
        exch_size = float(exch_size_str)
        exch_in_pos = abs(exch_size) > 0
        is_long = exch_size > 0
        if state.get('active_trade', False) and not exch_in_pos:
            logging.warning("Bot active but no exchange position found. Resetting state.")
            reset_state()
            state = get_state()
        elif not state.get('active_trade', False) and exch_in_pos:
            logging.error("Exchange position found, but bot inactive. Manual intervention needed. Bot exiting cycle.")
            return
        df = fetch_candles(exchange, SYMBOL, TIMEFRAME, FETCH_LIMIT)
        if df.empty or len(df) < FETCH_LIMIT:
            logging.warning(f"Insufficient candle data ({len(df)}). Skipping.")
            return
        df = compute_indicators(df, rsi_length=RSI_LENGTH, atr_length=ATR_LENGTH)
        df = detect_rsi_divergence(df, swing_window=SWING_WINDOW, align_window=3)
        df.dropna(inplace=True)
        latest = df.iloc[-1]
        price = latest['close']
        atr_val = latest['ATR']
        if state.get('active_trade', False):
            stop_loss_price = state.get('stop_loss_price')
            target_price = state.get('target_price')
            entry_price = state.get('entry_price')
            is_long = state['position_side'] == 'long'
            close_reason = None
            state = update_trailing_stop(state, price, atr_val)
            if (is_long and price <= state['trailing_stop_level']) or (not is_long and price >= state['trailing_stop_level']):
                close_reason = f"TRAILING STOP Hit! Price={price:.4f}, Trail={state['trailing_stop_level']:.4f}"
            elif (is_long and price >= target_price) or (not is_long and price <= target_price):
                close_reason = f"PROFIT TARGET Hit! Price={price:.4f}, TP={target_price:.4f}"
            if close_reason:
                if not state.get('closing', False):
                    state['closing'] = True
                    set_state(state)
                    print(f"\n{Fore.RED}{Style.BRIGHT}EXIT SIGNAL: {close_reason}. Closing {state['position_side']} position.{Style.RESET_ALL}")
                    logging.info(f"EXIT SIGNAL: {close_reason}. Closing {state['position_side']} position.")
                    for oid in [state.get('sl_order_id'), state.get('tp_order_id')]:
                        if oid:
                            try:
                                exchange.cancel_order(oid, SYMBOL)
                                logging.info(f"Cancelled open order: {oid}")
                            except Exception as e:
                                logging.warning(f"Failed to cancel order {oid}: {e}")
                    side = 'sell' if is_long else 'buy'
                    try:
                        params = {'reduceOnly': True}
                        order = exchange.create_market_order(SYMBOL, side, abs(exch_size), params=params)
                        logging.info(f"Market close order placed: {order.get('id', 'N/A')}")
                        reset_state()
                        print(f"{Fore.MAGENTA}Position closed successfully. State reset.{Style.RESET_ALL}")
                    except Exception as e:
                        logging.error(f"Market close FAILED: {e}")
                return
            else:
                set_state(state)
                profit_pct = ((price / entry_price - 1) * 100) if is_long else ((entry_price / price - 1) * 100)
                profit_color = Fore.GREEN if profit_pct > 0 else Fore.RED
                print(f"{Fore.CYAN}Active {Fore.GREEN if is_long else Fore.RED}{state['position_side'].upper()} position: Entry={entry_price:.4f}, Current={price:.4f}, SL={stop_loss_price:.4f}, TP={target_price:.4f}, Trail={state['trailing_stop_level']:.4f}, P/L: {profit_color}{profit_pct:.2f}%")
                logging.info("Holding position. No exit signal.")
        elif not state.get('active_trade', False):
            if latest['bullish_div']:
                side = 'buy'
                pos_side = 'long'
            elif latest['bearish_div']:
                side = 'sell'
                pos_side = 'short'
            else:
                print(f"{Fore.YELLOW}No entry conditions met.{Style.RESET_ALL}")
                logging.info("No entry conditions met.")
                return
            try:
                min_amount = MIN_AMOUNT
                min_notional = MIN_NOTIONAL
                price_decimals = step_to_decimals(PRICE_PRECISION)
                amount_decimals = step_to_decimals(AMOUNT_PRECISION)
                min_usd = min_notional * 1.1
                order_size_usd = max(ORDER_SIZE_USD, min_usd)
                amount = order_size_usd / price
                amount = float(f"{amount:.{amount_decimals}f}")
                if amount < min_amount or (amount * price) < min_notional:
                    logging.error(f"Calculated amount {amount} (notional ${amount*price:.2f}) is below Bybit minimums (amount {min_amount}, notional ${min_notional}). Skipping entry.")
                    print(f"{Fore.RED}{Style.BRIGHT}Order size too small for Bybit minimums. Skipping entry.{Style.RESET_ALL}")
                    return
                logging.info(f"Attempting {side.upper()} entry: {amount} {SYMBOL.split('/')[0]} @ Market (min amount: {min_amount}, min notional: {min_notional})")
                params = {}
                order = exchange.create_market_order(SYMBOL, side, amount, params=params)
                logging.info(f"Entry order placed: {order.get('id', 'N/A')}")
                print(f"{Fore.GREEN}Entry order placed: {order.get('id', 'N/A')}")
                if side == 'buy':
                    stop_loss_price = price - max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    target_price = price + price * PROFIT_TARGET_PCT
                    trailing_stop_level = price - max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    highest = price
                    lowest = None
                else:
                    stop_loss_price = price + max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    target_price = price - price * PROFIT_TARGET_PCT
                    trailing_stop_level = price + max(ATR_MULTIPLIER * atr_val, price * STOP_LOSS_PCT)
                    highest = None
                    lowest = price
                price_decimals = step_to_decimals(PRICE_PRECISION)
                stop_loss_price = float(f"{stop_loss_price:.{price_decimals}f}")
                target_price = float(f"{target_price:.{price_decimals}f}")
                trailing_stop_level = float(f"{trailing_stop_level:.{price_decimals}f}")
                trigger_direction = -1 if side == 'buy' else 1
                sl_order = exchange.create_order(
                    SYMBOL, 'STOP_MARKET', 'sell' if side == 'buy' else 'buy', amount, None,
                    {
                        'stopPrice': stop_loss_price,
                        'reduceOnly': True,
                        'category': 'linear',
                        'orderType': 'Market',
                        'triggerDirection': trigger_direction,
                        'triggerBy': 'MarkPrice'
                    }
                )
                tp_order = exchange.create_order(
                    SYMBOL, 'TAKE_PROFIT_MARKET', 'sell' if side == 'buy' else 'buy', amount, None,
                    {
                        'stopPrice': target_price,
                        'reduceOnly': True,
                        'category': 'linear',
                        'orderType': 'Market',
                        'triggerDirection': trigger_direction,
                        'triggerBy': 'MarkPrice'
                    }
                )
                new_state = {
                    "active_trade": True,
                    "position_side": pos_side,
                    "entry_price": price,
                    "stop_loss_price": stop_loss_price,
                    "target_price": target_price,
                    "highest": highest,
                    "lowest": lowest,
                    "trailing_stop_level": trailing_stop_level,
                    "sl_order_id": sl_order.get('id'),
                    "tp_order_id": tp_order.get('id'),
                    "atr_at_entry": atr_val,
                    "closing": False
                }
                set_state(new_state)
                print(f"{Fore.YELLOW}Stop loss set at: {stop_loss_price:.4f}, Target: {target_price:.4f}, Trailing Stop (bot): {trailing_stop_level:.4f}{Style.RESET_ALL}")
                logging.info(f"SL order placed: {sl_order.get('id', 'N/A')} at {stop_loss_price}")
                logging.info(f"TP order placed: {tp_order.get('id', 'N/A')} at {target_price}")
                logging.info(f"State updated: {new_state}")
                time.sleep(5)
            except ccxt.InsufficientFunds as e:
                logging.error(f"Insufficient funds for entry: {e}")
                print(f"{Fore.RED}{Style.BRIGHT}Insufficient funds for entry: {e}{Style.RESET_ALL}")
            except Exception as e:
                logging.error(f"Entry error: {e}", exc_info=True)
                print(f"{Fore.RED}{Style.BRIGHT}Entry error: {e}{Style.RESET_ALL}")
    except Exception as e:
        logging.error(f"Unexpected Error in bot_logic: {e}", exc_info=True)
        print(f"{Fore.RED}{Style.BRIGHT}Unexpected Error in bot_logic: {e}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[{datetime.now().strftime('%H:%M:%S')}] Cycle completed {Style.RESET_ALL}")
    logging.info(f"--- RSI Divergence Cycle End ---\n")

def start_trailing_stop_thread():
    def loop():
        while True:
            try:
                trailing_stop_checker()
            except Exception as e:
                logging.error(f"TS Checker thread error: {e}")
            time.sleep(1)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

start_trailing_stop_thread()

print(f"\n{Fore.GREEN}{Style.BRIGHT}Starting RSI Divergence Bot for Bybit{Style.RESET_ALL}")
print(f"{Fore.CYAN}Checking conditions every {SCHEDULE_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.{Style.RESET_ALL}\n")
logging.info("Starting RSI Divergence Bot for Bybit")
schedule.every(SCHEDULE_INTERVAL_SECONDS).seconds.do(bot_logic)
bot_logic()
while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
        break
    except Exception as e:
        logging.critical(f"MAIN LOOP ERROR: {e}", exc_info=True)
        logging.info("Sleeping 60s...")
        time.sleep(60)
logging.info("Bot finished.")
