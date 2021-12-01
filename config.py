#fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule
fee = 0.001

use_testnet = True
reset_orders = True

if use_testnet:
	#KEYS FOR TESTNET
	api_key = "SAnwsmicA0Z35tMi4y4oIdbgVBzX6N6bLzEvUwcYDMXGk3XnUdEwN4NAFmNDy1aw"
	api_secret = "558hkhICPOKwrwsFEAvy0mDuGwALcWwTjeaTFhemUEapfmQJNo7TczXbNWbsH1JK"

	#WALLET ASSETS
	balance = {
				'USDT': 0,
				'BNB': 0 
				}
else:
	#KEYS FOR REAL SERVER
	api_key = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
	api_secret = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

	#WALLET ASSETS
	balance = {
				'USD': 0,
				'XLM': 0
				}


#store persistent values and constants that carry over between calls of trading_strategy()
carryover_vars = {
					'prev_max_balance': 0,
					'current_balance': 0,
					'buyprice': 0,
					'buytime': 0,
					'buycomm': 0,
					'buysize': 0,
					'sellprice': 0,
					'selltime': 0,
					'COMMRATE': 0.001,
					'buy_order_finished': 1,
					'sell_order_finished': 1,
					'sell_cond_count': [0,0,0,0]
}

#list of pairs to analyze
trading_pairs = [
				{	'pair': "BNBUSDT",		#change back to xlm
					'precision': 4,
					'baseAsset': "BNB",
					'quoteAsset': "USDT"

					}
]
