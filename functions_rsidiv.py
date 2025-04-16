import pandas as pd
import pandas_ta as ta
import logging
import ccxt

def fetch_candles(exchange, symbol, timeframe, limit):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)
        return df
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        logging.warning(f"CCXT Error fetching {symbol} {timeframe} candles: {e}")
    except Exception as e:
        logging.error(f"Unexpected error fetching candles: {e}", exc_info=True)
    return pd.DataFrame()

def compute_indicators(df, rsi_length=6, atr_length=6):
    try:
        df['RSI'] = ta.rsi(df['close'], length=rsi_length)
    except Exception as e:
        logging.error(f"Error calculating RSI: {e}")
    try:
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=atr_length)
    except Exception as e:
        logging.error(f"Error calculating ATR: {e}")
    return df

def find_local_extrema(series, order=4, mode='max'):
    import numpy as np
    from scipy.signal import argrelextrema
    if mode == 'max':
        idx = argrelextrema(series.values, np.greater_equal, order=order)[0]
    else:
        idx = argrelextrema(series.values, np.less_equal, order=order)[0]
    extrema = np.zeros(series.shape, dtype=bool)
    extrema[idx] = True
    return pd.Series(extrema, index=series.index)

def detect_rsi_divergence(df, swing_window=4, align_window=3):
    import numpy as np
    df['local_max'] = find_local_extrema(df['close'], order=swing_window, mode='max')
    df['local_min'] = find_local_extrema(df['close'], order=swing_window, mode='min')
    df['rsi_local_max'] = find_local_extrema(df['RSI'], order=swing_window, mode='max')
    df['rsi_local_min'] = find_local_extrema(df['RSI'], order=swing_window, mode='min')
    df['bullish_div'] = False
    df['bearish_div'] = False
    price_min_idx = np.where(df['local_min'].values)[0]
    price_max_idx = np.where(df['local_max'].values)[0]
    rsi_min_idx = np.where(df['rsi_local_min'].values)[0]
    rsi_max_idx = np.where(df['rsi_local_max'].values)[0]
    for i in price_min_idx:
        rsi_nearby = rsi_min_idx[(rsi_min_idx >= i-align_window) & (rsi_min_idx <= i+align_window)]
        if len(rsi_nearby) == 0:
            continue
        rsi_idx = rsi_nearby[np.argmin(np.abs(rsi_nearby-i))]
        prev_price = price_min_idx[price_min_idx < i]
        if len(prev_price) == 0:
            continue
        prev_price_idx = prev_price[-1]
        prev_rsi = rsi_min_idx[rsi_min_idx < rsi_idx]
        if len(prev_rsi) == 0:
            continue
        prev_rsi_idx = prev_rsi[-1]
        if df['close'].iloc[i] < df['close'].iloc[prev_price_idx] and df['RSI'].iloc[rsi_idx] > df['RSI'].iloc[prev_rsi_idx]:
            df.at[df.index[i], 'bullish_div'] = True
    for i in price_max_idx:
        rsi_nearby = rsi_max_idx[(rsi_max_idx >= i-align_window) & (rsi_max_idx <= i+align_window)]
        if len(rsi_nearby) == 0:
            continue
        rsi_idx = rsi_nearby[np.argmin(np.abs(rsi_nearby-i))]
        prev_price = price_max_idx[price_max_idx < i]
        if len(prev_price) == 0:
            continue
        prev_price_idx = prev_price[-1]
        prev_rsi = rsi_max_idx[rsi_max_idx < rsi_idx]
        if len(prev_rsi) == 0:
            continue
        prev_rsi_idx = prev_rsi[-1]
        if df['close'].iloc[i] > df['close'].iloc[prev_price_idx] and df['RSI'].iloc[rsi_idx] < df['RSI'].iloc[prev_rsi_idx]:
            df.at[df.index[i], 'bearish_div'] = True
    return df
