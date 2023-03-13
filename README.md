# binance_autotrader_python

Dependencies:
  Backtrader scripts:
    backtrader
    ta-lib
    matplotlib
  
  Binance Autotrader:
    ta-lib
    binance python api
    

    
get_data - gets historical data from binance for use in backtrader scripts

backtestgeneric - generic script for testing various strategies (put in buy_logic(), sell_logic()

backtest_stoplimit_ema_cross - actual verified best strategy so far, 10k -> 42k in 3 years

-----
backtestXXXX - various backtesting strategies, EMABest is best so far. RSI did well with a million microtrades until commissions are added into the mix

binance trading pairs - list of binance trading pairs so dont have to get_exchange_info

binance_autotraderEMA - implementation of best backtest strat. ideally will be able to be easily adapted to new strategies
