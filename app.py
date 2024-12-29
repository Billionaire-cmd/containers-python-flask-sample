from flask import Flask, request, jsonify
import pandas as pd
import MetaTrader5 as mt5
import talib

app = Flask(__name__)

@app.route("/trade", methods=["POST"])
def trade():
    try:
        # Parse input parameters
        data = request.get_json()
        mt5_login = data.get("mt5_login")
        mt5_password = data.get("mt5_password")
        mt5_server = data.get("mt5_server")
        license_key = data["license_key"]
        symbol = data["symbol"]
        timeframe = data["timeframe"]
        lot_size = float(data["lot_size"])
        take_profit = float(data["take_profit"])
        stop_loss = float(data["stop_loss"])
        trailing_stop = data.get("trailing_stop", False)

        # Validate inputs
        if not (mt5_login and mt5_password and mt5_server):
            return jsonify({"error": "MT5 login details are missing"}), 400

        # Initialize MetaTrader 5
        if not mt5.initialize(login=mt5_login, password=mt5_password, server=mt5_server):
            return jsonify({"error": f"MT5 initialization failed: {mt5.last_error()}"}), 500

        # Get price data
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)
        if rates is None or len(rates) == 0:
            return jsonify({"error": "Failed to retrieve market data"}), 500
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')

        # Calculate indicators
        df['RSI'] = talib.RSI(df['close'], timeperiod=14)
        df['EMA_10'] = talib.EMA(df['close'], timeperiod=10)
        df['EMA_30'] = talib.EMA(df['close'], timeperiod=30)
        df['EMA_50'] = talib.EMA(df['close'], timeperiod=50)
        df['EMA_90'] = talib.EMA(df['close'], timeperiod=90)

        upper_band, _, lower_band = talib.BBANDS(
            df['close'], timeperiod=6, nbdevup=0.6, nbdevdn=0.6, matype=0
        )
        df['BB_upper'] = upper_band
        df['BB_lower'] = lower_band

        # Get latest data
        latest = df.iloc[-1]
        action = None

        # Trading strategy logic
        rsi = latest['RSI']
        if rsi <= 16 and latest['close'] < latest['BB_lower'] and latest['EMA_10'] > latest['EMA_30']:
            action = "BUY"
        elif rsi >= 85 and latest['close'] > latest['BB_upper'] and latest['EMA_10'] < latest['EMA_30']:
            action = "SELL"

        if not action:
            return jsonify({"message": "No trade conditions met", "trade": None}), 200

        # Define request parameters for trade
        price = mt5.symbol_info_tick(symbol).ask if action == "BUY" else mt5.symbol_info_tick(symbol).bid
        sl = price - stop_loss if action == "BUY" else price + stop_loss
        tp = price + take_profit if action == "BUY" else price - take_profit

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_BUY if action == "BUY" else mt5.ORDER_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 123456,
            "comment": "Advanced Trade Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC
        }

        # Send trade request
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({"error": f"Trade failed: {result.retcode}"}), 500

        # Handle trailing stop if enabled
        if trailing_stop:
            handle_trailing_stop(symbol, action, lot_size)

        return jsonify({
            "message": f"Trade executed successfully: {action}",
            "symbol": symbol,
            "action": action,
            "volume": lot_size,
            "price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "result": result._asdict()
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        mt5.shutdown()


def handle_trailing_stop(symbol, action, lot_size):
    """Implements a trailing stop mechanism."""
    while True:
        # Get the current price
        tick = mt5.symbol_info_tick(symbol)
        current_price = tick.ask if action == "BUY" else tick.bid

        # Check open positions
        positions = mt5.positions_get(symbol=symbol)
        if len(positions) == 0:
            break

        # Calculate trailing stop
        for position in positions:
            sl = position.sl
            if action == "BUY":
                new_sl = max(sl, current_price - trailing_stop)
                if new_sl != sl:
                    modify_sl(position, new_sl)
            elif action == "SELL":
                new_sl = min(sl, current_price + trailing_stop)
                if new_sl != sl:
                    modify_sl(position, new_sl)


def modify_sl(position, new_sl):
    """Modifies the stop-loss of an open position."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "sl": new_sl,
        "tp": position.tp
    }
    mt5.order_send(request)


if __name__ == "__main__":
    app.run(debug=True)
