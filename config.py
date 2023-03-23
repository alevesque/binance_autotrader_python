# -*- coding: utf-8 -*-
"""
Created on Tue Mar 14 15:11:08 2023

@author: alevesque
"""

#fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule
fee = 0.001

use_testnet = True
reset_orders = True

if use_testnet:
	#KEYS FOR TESTNET
	API_KEY = "SAnwsmicA0Z35tMi4y4oIdbgVBzX6N6bLzEvUwcYDMXGk3XnUdEwN4NAFmNDy1aw"
	API_SECRET = "558hkhICPOKwrwsFEAvy0mDuGwALcWwTjeaTFhemUEapfmQJNo7TczXbNWbsH1JK"

	#WALLET ASSETS
	balance = {
				'USDT': 0,
				'BNB': 0 
				}
else:
	#KEYS FOR REAL SERVER
	API_KEY = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
	API_SECRET = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

	#WALLET ASSETS
	balance = {
				'USD': 0,
				'XLM': 0
				}

ALIVE_ORDER_STATUS = ['NEW', 'PARTIALLY_FILLED']

# acceptable parameters for api create_order() method
API_ORDER_PARAMS = ['symbol', 
                 'side', 
                 'type', 
                 'timeInForce', 
                 'quantity', 
                 'quoteOrderQty', 
                 'price', 
                 'newClientOrderId', 
                 'icebergQty', 
                 'newOrderRespType', 
                 'recvWindow',
                 ]
order_opts = {
    'symbol': '', 
    'side': 'SELL',
    'type': 'LIMIT', #limit, market, etc
    'timeInForce': 'GTC',
    'quantity': 0.0,
    'price': 0.0,
    # 'newClientOrderId': 0, # optional, autogen by api
    'time': 0,
    'status': 'NEW',
    'commission': 0.0
    }

# list of pairs to analyze
trading_pairs = {
    'BNBUSDT':  # change back to xlm
				{
					'precision': 4,
					'baseAsset': "BNB",
					'quoteAsset': "USDT"

					},
    'XLMUSD':  # change back to xlm
                {
                	'precision': 4,
                	'baseAsset': "XLM",
                	'quoteAsset': "USD"
                
                	},                
    'VTHBTC':  # change back to xlm
    				{
    					'precision': 4,
    					'baseAsset': "VTH",
    					'quoteAsset': "BTC"
    
    					},
    'LTCBUSD':  # change back to xlm
    				{
    					'precision': 4,
    					'baseAsset': "LTC",
    					'quoteAsset': "BUSD"
    
    					},
}

intervals = ['5m', '15m']

class Order():
    def __init__(self):
        self.order = {}
        self.reset()
    
    def set(self, **kwargs):
        for kw in kwargs:
            self.order[kw] = kwargs[kw]
        
    def reset(self):
        self.order.clear()
        self.set(
            **{
                'symbol': None,
                'side': None, #buy/sell
                'type': None, #limit, market, etc
                'timeInForce': None,
                'quantity': 0.0,
                'price': 0,
                'orderId': 0,
                'time': 0,
                'status': None,
                'commission': 0
                }
            )
    
    def alive(self):
        if self.order['status'] in ALIVE_ORDER_STATUS:
            return True
        else:
            return False
