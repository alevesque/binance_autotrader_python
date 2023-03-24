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
        'USD': 0,
        'BNB': 0,
        'LTC': 0,
        'BUSD': 0,
        'VTH': 0,
        'BTC': 0,

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

# order statuses that mean the order is not closed out
ALIVE_ORDER_STATUS = ['NEW', 'PARTIALLY_FILLED']

# acceptable parameters for api create_order() method
API_ORDER_PARAMS = [
    'symbol',
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
