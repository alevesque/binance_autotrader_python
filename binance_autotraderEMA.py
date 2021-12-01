#from binance.client import Client
from binance.exceptions import *
#import time
import datetime, requests, asyncio, signal, warnings, config
#import sys
#import requests
import numpy as np
import pandas as pd
from talib import abstract
#import asyncio
#import json
from binance import AsyncClient, BinanceSocketManager


with warnings.catch_warnings():
    warnings.simplefilter('ignore', RuntimeWarning)
"""
==============================================================================================
Binance Autotrader - EMA Strategy
==============================================================================================

"""


"""
TODO:
[X]	1. implement talib - get data from binance 
[X]		1a. websocket live stream of data in real time is better than querying indicators every minute bc could be late
[X] 2. implement output/logging to check if indicators are accurate
[X] 3. implement buy/sell order notify
[X] 4. once confident algorithm works, activate buy/sell (testnet server?)
[X] 	4a. intermediate info (ie order placed notif) while waiting for partial fills? may not matter when ticks are faster
[X]		4b. maybe make sell market (remove timeInForce from **args somehow) so it works faster and can see if operation is smooth 
[X]		4c. need way to close connection at interrupt b/c not doing so is causing weird behavior with ws possibly left open
[ ] 4. implement html frontend
[ ] 5. implement remote accessible server page
[ ] 6. asst optimizations
[X]		6a. close conn on keyboard interrupt (main while loop?); current try/except doesnt work
[ ]		6b. diff placement of abstract talib load may increase speed - currently unecessary bc limiting factor is server response
[ ]		6c. indicators data structure? (0: is weird indexing but works fine as is)
[X]		6d. auto dl and parse exchange info to get qty limit for each asset - prob not necc in practice but good for down the line if accumulate a lot
[ ]		6e. buy/sell position check - use min position of exchangeinfo asset
[ ]		6f. NEEDS FIX - net gain %

"""

'''
[ ]	possible upgrade - actively scan several pairs for ema crossover, buy, sell after certain % gain or other condition
[ ]	captures the daily +15% swings of random currencies on binance "top gainers"

	multi-logging could look like:

	PAIR1 ----
	Pair2 ----
	Pair3 ----

	===========

	Pair1 ----
	Pair2 ----
	Pair3 ----

'''
async def main(*args):
	
	if(loop_active_bool==True):
		#initialize websocket client to livestream data one ping at a time. preferable to 
		#downloading redundant or late data as would happen with manual update periods using regular client
		async_client = await AsyncClient.create(api_key=config.api_key,api_secret=config.api_secret,testnet=config.use_testnet,tld='us')
		
		if config.reset_orders==True:
			open_orders = await async_client.get_open_orders()
			await close_all_open_orders(async_client,open_orders)
			print('All open orders closed.')
	else:
		exit(0)
	
	bm = BinanceSocketManager(async_client)
	

	exchangeinfo_raw = await async_client.get_exchange_info()
	exchangeinfo = []
	#download initial data for the longer EMA timeframes, so calculations can start immediately
	#rather than spooling up hundreds of ticks of price data for EMA600, 400 etc
	for x in config.trading_pairs:
		init_data_raw = await async_client.get_klines(symbol=x['pair'],interval='5m',limit=601)
		data = pd.DataFrame(np.array(init_data_raw)[:,0:6],
				   columns=['open_time', 'open', 'high', 'low', 'close', 'volume'],
				   dtype='float64')
		print(f"Initial data loaded - {x['pair']}")
	
		for pairs in exchangeinfo_raw['symbols']: 
			if pairs['symbol'] == x['pair']:
				exchangeinfo.append(pairs)
		
	#print(exchangeinfo)

	# start any sockets here, i.e. a trade socket
	xlmusd_kline_5m = bm.kline_socket(symbol=config.trading_pairs[0]['pair'],interval='5m')
	trade_manager = bm.user_socket()
	print('Sockets initialized.')

	# then start receiving messages
	async with xlmusd_kline_5m as xlmusd_kline_5m_message:
		while loop_active_bool==True:

			#-----
			#take in new kline data from websocket stream
			new_data_raw = await xlmusd_kline_5m_message.recv()
			
			#process raw data into simpler form
			data = process_raw_klines(new_data_raw,data)
			#------

			#------
			#get account info for balance checking
			acc_res = await async_client.get_account(recvWindow=10000)

			#update wallet balances
			balance = wallet_update(acc_res)
			print(balance)
			#------

			#send data to functions that calc indicators, calc strat, buy/sell if necessary, and log to console
			[indicators, order, config.carryover_vars] = trading_strategy(data,config.trading_pairs,balance,config.carryover_vars,exchangeinfo[0])
			
			#if trading_strategy() decided conditions warrant an order, place order
			if order['side'] != 'none':
				order = await place_order(async_client, symbol=order['symbol'],side=order['side'],type=order['type'],timeInForce=order['timeInForce'],quantity="{0:.{1}f}".format(order['quantity'],config.trading_pairs[0]['precision']),price="{:4f}".format(order['price']))#,newOrderRespType='RESULT')
				
				#if above order wasn't immediately filled, give updates on partial fills
				if order['status'] != "FILLED" and order['status'] != "CANCELED" and order['status'] != "EXPIRED":
					
					log_data(indicators,data['close'].values[-1],data['open_time'].values[-1]/1000,order,config.carryover_vars)
					async with trade_manager as order_response:
						while order['status'] != "FILLED" and order['status'] != "CANCELED" and order['status'] != "EXPIRED":
							
							order_result_raw = await order_response.recv()
							
							#if update is from order execution (dont care about other account updates on this stream)
							if order_result_raw['e'] == 'executionReport':
								order = order_stream_data_process(order_result_raw,datastream='Type2')
								
								log_data(indicators,data['close'].values[-1],data['open_time'].values[-1]/1000,order,config.carryover_vars)

				
			#log data and return updated persistent variables (order b/s complete)
			config.carryover_vars = log_data(indicators,data['close'].values[-1],data['open_time'].values[-1]/1000,order,config.carryover_vars)


async def place_order(async_client,**order_parameters):
	
	if order_parameters['type']=='MARKET':
		del order_parameters['timeInForce']
		del order_parameters['price']
	
	try:
		order_result_raw = await async_client.create_order(**order_parameters)
		order_result = order_stream_data_process(order_result_raw,datastream='Type1')			
		return order_result

	except BinanceAPIException as e:
		print(e)
		print("placing order - something went wrong.")
		#time.sleep(1)
		return -1
	except requests.exceptions.RequestException as e:
		print(e)
		print("Error - Check connection - placing order")
		#time.sleep(1)
		return -1

async def close_all_open_orders(async_client,open_orders):
	#print(open_orders)
	for order in open_orders:
		print(f"Order closed: Symbol: {order['symbol']} OrderId: {order['orderId']}")
		await async_client.cancel_order(symbol=order['symbol'],orderId=order['orderId'])
	return

def order_stream_data_process(order_result_raw,datastream):
	if datastream == 'Type1':
		comm = 0
		avg_price = 0
		i = 0
		for x in order_result_raw['fills']:
			comm += float(x['commission'])
			avg_price += float(x['price'])
			i += 1
		if i > 0:
			avg_price = avg_price/i
		order_result = {
						'symbol': order_result_raw['symbol'],
						'side': order_result_raw['side'],
						'type': order_result_raw['type'],
						'timeInForce': order_result_raw['timeInForce'],
						'quantity': order_result_raw['executedQty'],
						'price': avg_price,#order_result_raw['price'],
						'orderId': order_result_raw['orderId'],
						'time': order_result_raw['transactTime'],
						'status': order_result_raw['status'],
						'commission': comm
						}

	elif datastream == 'Type2':
		order_result = {
						'symbol': order_result_raw['s'],
						'side': order_result_raw['S'],
						'type': order_result_raw['o'],
						'timeInForce': order_result_raw['f'],
						'quantity': order_result_raw['z'],
						'price': order_result_raw['p'],
						'orderId': order_result_raw['i'],
						'time': order_result_raw['E'],
						'status': order_result_raw['X'],
						'commission': order_result_raw['n']
						}

	return order_result

def process_raw_klines(new_data_raw,data):

	#take only relevant data (OHLC, volume). vol prob not necessary but optimize later
	#shit bruh OHL prob unnecessary too
	new_data = {
			'open_time': new_data_raw['k']['t'],
			'open': new_data_raw['k']['o'],
			'high': new_data_raw['k']['h'],
			'low': new_data_raw['k']['l'],
			'close': new_data_raw['k']['c'],
			'volume': new_data_raw['k']['v'],
	}
	
	#add new data tick onto existing data set and remove the 0th line to avoid dataset getting huge and
	#overwhelming memory. keep only what is needed for biggest indicator. i.e. 600 lines for EMA600
	data = data.append(new_data,ignore_index=True)
	data = data.drop(0)
	#convert to float64 so talib doesn't bitch
	data = data.astype('float64')
	#note: resulting dataframe is 1-indexed not zero

	return data

#update wallet values with latest balances
def wallet_update(acc_res):

	if "balances" in acc_res:
		for bal in acc_res['balances']:
			for bal2 in config.balance:
				if bal['asset'] == bal2:
					config.balance[bal2] = bal['free']
		return config.balance
	else:
		print('Error - no balances in Account')
		return -1


def indicator_data(data):
	#only load talib when needed - helps memory but maybe adds to lag?
	TALib_EMA = abstract.EMA
	
	#calculate indicators
	EMA600 = TALib_EMA(data['close'].values,timeperiod=600)
	EMA400 = TALib_EMA(data['close'].values,timeperiod=400)
	EMA100 = TALib_EMA(data['close'].values,timeperiod=100)
	EMA50 = TALib_EMA(data['close'].values,timeperiod=50)
	EMA25 = TALib_EMA(data['close'].values,timeperiod=25)
	
	indicators = {}
	#Index note: indicators[1] == EMAXX[-1] and indicators[0] == EMAXX[-2])
	for i in range(2):
		indicators[i] = {   					
						'EMA25': EMA25[i-2],
						'EMA50': EMA50[i-2],
						'EMA100': EMA100[i-2],
						'EMA400': EMA400[i-2],
						'EMA600': EMA600[i-2]
						}
	return indicators
	

def trading_strategy(data,trading_pair,balance,carryover_vars,symbolinfo):
	
	#send data to function to calculate indicators
	indicators = indicator_data(data)
	
	#reset order info template - if no B/S order is warranted, the empty values will notify other functions
	#that there wasn't an order this tick
	order = {
			'symbol': 'none',
			'side': 'none', #buy/sell
			'type': 'none', #limit, market, etc
			'timeInForce': 'none',
			'quantity': 0,
			'price': 0,
			'orderId': 0,
			'time': 0,
			'status': '',
			'commission': 0
			}

	#shorter variable for asset balance - just so B/S logic is simpler to read
	bal = float(balance[trading_pair[0]['baseAsset']]) #change back to xlm
	#####################
	# BUYING CONDITIONS #
	#####################
	#remember to reset - uncomment two ema ifs, and double indent everything below in the buy section
	if bal < 1: #no position
		if carryover_vars['buy_order_finished'] == 1:
			#if indicators[1]['EMA400'] > indicators[1]['EMA600']:
			#	if indicators[0]['EMA50'] < indicators[0]['EMA100'] and indicators[1]['EMA50'] > indicators[1]['EMA100']: #BEST
			#change below back to balance['USD']
			
			carryover_vars['buysize'] = float("{:.0f}".format(0.98*float(balance[trading_pair[0]['quoteAsset']])/data['close'].values[-1]))
			for filters in symbolinfo['filters']:
				if filters['filterType'] == 'LOT_SIZE':
					if carryover_vars['buysize'] > float(filters['maxQty']):
						carryover_vars['buysize'] = float(filters['maxQty'])

			carryover_vars['buyprice'] = float("{:.4f}".format(data['close'].values[-1]))
			carryover_vars['buycomm'] = carryover_vars['buysize']*carryover_vars['buyprice']*carryover_vars['COMMRATE']
			carryover_vars['buy_order_finished'] = 0
			
			#===========
			#BUY ACTIONS
			#===========
			order = {
				'symbol': trading_pair[0]['pair'], #change back to XLMUSD
				'side': 'BUY', #buy/sell
				'type': 'LIMIT', #limit, market, etc
				'timeInForce': 'GTC',
				'quantity': carryover_vars['buysize'],
				'price': carryover_vars['buyprice'],
				'orderId': 0,
				'time': 0,
				'status': 'test',
				'commission': carryover_vars['buycomm']
				}
			
			
			#self.buy(exectype=bt.Order.Limit,price=buyprice,size=buysize,valid=bt.date2num(data.num2date())+(5/1440))
			
					 
	######################
	# SELLING CONDITIONS #
	######################
	#remember to reset conditions - uncomment two ifs and double indent sell action downward
	elif bal >= 1: #position
		
		if carryover_vars['sell_order_finished'] == 1:
			last_closing_price=data['close'].values[-1]
			#if indicators[0]['EMA25'] > indicators[0]['EMA50'] and indicators[1]['EMA25'] < indicators[1]['EMA50']: #BEST
			#	if (last_closing_price > round(float("{:.6f}".format(carryover_vars['buyprice'] + carryover_vars['COMMRATE']*(carryover_vars['buyprice']+last_closing_price))),4)):
			#============
			#SELL ACTIONS
			#============
			sellQty = bal
			sellType = 'LIMIT'
			for filters in symbolinfo['filters']:
				if sellType == 'MARKET':
					if filters['filterType'] == 'MARKET_LOT_SIZE':
						if sellQty > float(filters['maxQty']):
							sellQty = float(filters['maxQty'])
				else:
					if filters['filterType'] == 'LOT_SIZE':
						if sellQty > float(filters['maxQty']):
							sellQty = float(filters['maxQty'])
			order = {
				'symbol': trading_pair[0]['pair'], #change back to XLMUSD
				'side': 'SELL', #buy/sell
				'type': sellType, #limit, market, etc
				'timeInForce': 'GTC',
				'quantity': sellQty,
				'price': last_closing_price,
				'orderId': 0,
				'time': 0,
				'status': 'test',
				'commission': 0
				}			
			carryover_vars['sell_order_finished'] = 0
	
	
	
	return indicators,order,carryover_vars
	

def log_data(indicators,close_price,tick_time,order,carryover_vars):
	
	#if an order is currently active, log relevant info to console	
	if order['status'] != '':

		order['price'] = float(order['price'])
		order['quantity'] = float(order['quantity'])
		
		

		output = (
					f"OrderID: {order['orderId']:3d} Type: {order['symbol']:<2} {order['type']:<2} " + 
					f"{order['side']:<2} ".ljust(5) +
					f"Status: " +
					f"{order['status']:1} ".ljust(17) +
					f"Size: ".ljust(6) +
					f"{order['quantity']:6.2f} ".rjust(8) +
					f"Price: {order['price']:6.4f} " +
					f"Cost: {order['quantity']*order['price']:6.2f} " +
					f"Comm: {order['quantity']*order['price']*carryover_vars['COMMRATE']:4.2f} "
				)					
		
		if order['status'] == "FILLED":
			if order['side'] == 'SELL':
				output += ( #change net gain to: 100* (new bal - old bal) / old bal
							f"net gain: {((order['price']-carryover_vars['buyprice'])*order['quantity'] - order['quantity']*order['price']*carryover_vars['COMMRATE'] - carryover_vars['buycomm']):4.2f} " + 
							f"net gain %: {(100*(((order['price']-carryover_vars['buyprice'])*carryover_vars['buysize'] - order['commission'] - carryover_vars['buycomm'])/(carryover_vars['buysize']*carryover_vars['buyprice']))):3.2f}% "
							)
				carryover_vars['sell_order_finished'] = 1
			else:
				carryover_vars['buy_order_finished'] = 1
		elif order['status'] == 'CANCELED' or order['status'] == 'EXPIRED':
			if order['side'] == 'SELL':
				carryover_vars['sell_order_finished'] = 1
			else:
				carryover_vars['buy_order_finished'] = 1
		
	else:
		output = (
					f"EMA600: {indicators[1]['EMA600']:6.4f} " +
					f"EMA400: {indicators[1]['EMA400']:6.4f} " +
					f"EMA100: {indicators[1]['EMA100']:6.4f} " +
					f"EMA50: {indicators[1]['EMA50']:6.4f} " +
					f"EMA25: {indicators[1]['EMA25']:6.4f} " +
					f"Close Price: {close_price:6.4f} "
					)
	dt = datetime.datetime.fromtimestamp(tick_time)

	print('%s, %s, %s' % (dt.date().isoformat(), dt.time(),output))
	return carryover_vars

if __name__ == "__main__":

	loop = asyncio.get_event_loop()
	try:
		loop_active_bool = True
		loop.run_until_complete(main(loop_active_bool))
	except KeyboardInterrupt:
		loop_active_bool = False
		loop.run_until_complete(main(loop_active_bool))
	finally:
		print('Program stopped by operator - exiting cleanly.')