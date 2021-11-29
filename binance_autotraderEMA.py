from binance.client import Client
from binance.exceptions import *
#from binance.enums import *
import time
import datetime
import sys
import requests
import numpy as np
import pandas as pd
from talib import abstract
import asyncio
import json
from binance import AsyncClient, BinanceSocketManager

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
[ ] 3. implement buy/sell order notify
[ ] 4. once confident algorithm works, activate buy/sell (testnet server?)
[ ] 4. implement html frontend
[ ] 5. implement remote accessible server page
[ ] 6. asst optimizations - websockets for init data dl, diff placement of abstract talib load may increase speed, indicators data structure (0: is weird indexing)"""

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

prev_max_balance = 0
current_balance = 0
buyprice = 0
buytime = 0 
buycomm = 0
buysize = 0
sellprice = 0
selltime = 0
COMMRATE = 0.001
buy_order_finished = 1
sell_order_finished = 1
sell_cond_count = [0,0,0,0]





async def main():

	"""fee can be reduced by extensive BNB holdings, high monthly trade volume (USD), or 25% discount for paying fees with BNB. see https://www.binance.us/en/fee/schedule"""
	fee = 0.001

	api_key = "3dUerAA0KFYj9BPhLajkRdx3yxcxgSWqyayemilGsFft4lmyXc9iqyATO5D5xVDX"
	api_secret = "8CgimgMn9JFajxCxfO49bAz1Sx8yGSxUuoCyg3Mix1i7SQlzXzuvBiWnVFBcBXAC"

	#initialize regular API client to download initial data for the longer EMA timeframes, so calculations can
	#start immediately rather than spooling up hundreds of ticks of price data for EMA600, 400 etc
	try:
		#tld='' is used to denote which server to use (.com or .us)
		client = Client(api_key, api_secret,{"timeout": 6},tld='us')
		print("Client Initialized Successfully")
	except requests.exceptions.RequestException as e:
		print("Connection error - Initializing API client")
		sys.exit()

	#list of pairs to analyze
	trading_pairs = [("XLMUSD")]

	#download initial data for trading pairs then close regular client
	for x in trading_pairs:
			
		try:
			init_data_raw = client.get_klines(symbol=x,interval='5m',limit=601)
			data = pd.DataFrame(np.array(init_data_raw)[:,0:6],
				   columns=['open_time', 'open', 'high', 'low', 'close', 'volume'],
				   dtype='float64')
			client.close_connection()
			print('Initial data loaded, API client connection closed.')
		except BinanceAPIException as e:
			print(e)
			print("Querying prices - something went wrong, likely connection issues. Trying again in 5s.")
			time.sleep(5)
			break
		except requests.exceptions.RequestException as e:
			print(e)
			print("Error - Check connection - Querying prices")
			time.sleep(5)
			break


	#initialize websocket client to livestream data one ping at a time. preferable to 
	#downloading redundant or late data as would happen with manual update periods using regular client
	async_client = await AsyncClient.create(api_key=api_key,api_secret=api_secret,testnet=False,tld='us')
	bm = BinanceSocketManager(async_client)

	
	# start any sockets here, i.e. a trade socket
	xlmusd_kline_5m = bm.kline_socket(symbol='XLMUSD',interval='5m')
	trade_manager = bm.user_socket()
	print('sockets initialized')

	# then start receiving messages
	async with xlmusd_kline_5m as xlmusd_kline_5m_message:
		while True:
			new_data_raw = await xlmusd_kline_5m_message.recv()
			
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
			
			#send data to functions that calc indicators, calc strat, buy/sell if necessary, and log to console
			acc_res = await async_client.get_account(recvWindow=10000)
			
			balance = {
						'usd': 0,
						'xlm': 0
			}

			if "balances" in acc_res:
				#balance = acc_res['balances']
				for bal in acc_res['balances']:
					if bal['asset'].lower() == 'usd':
						balance['usd'] = bal['free']
					elif bal['asset'].lower() == 'xlm':
						balance['xlm'] = bal['free']

			
			indicators = trading_strategy(data,trading_pairs[0],balance)
			log_data(indicators,data['close'].values[-1],data['open_time'].values[-1]/1000)

	#close connection cleanly at end
	await async_client.close_connection()


	#place_order(symbol=x[1],side='BUY',type='LIMIT',timeInForce='GTC',quantity="{0:.{1}f}".format(float(USD_per_BTC_order["executedQty"])/float(BTC_per_alt["price"]),alt_precision[x[2]]),price="{:fadshflashdlaf}".format(float(BTC_per_alt["price"])))#,"{:.4f}".format(float(BTC_per_alt["price"])))
	
'''
def place_order(**order_parameters): #symbol, side, type, timeInForce,quantity,price):
	print(order_parameters)

	try:
		order = client.create_order(**order_parameters)
		return order
	except BinanceAPIException as e:
		print(e)
		print("placing order - something went wrong.")
		time.sleep(1)
		return -1
	except requests.exceptions.RequestException as e:
		print(e)
		print("Error - Check connection - placing order")
		time.sleep(1)
		return -1
'''



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
	#indicators[1] == EMAXX[-1] and indicators[0] == EMAXX[-2])
	for i in range(2):
		indicators[i] = {   					
						'EMA25': EMA25[i-2],
						'EMA50': EMA50[i-2],
						'EMA100': EMA100[i-2],
						'EMA400': EMA400[i-2],
						'EMA600': EMA600[i-2]
						}
	print(indicators)
	return indicators
	
'''
def sellcount(orderinfo):
	sellcode=orderinfo[0][1][1]
	global sell_cond_count
	sell_cond_count[sellcode-1] += 1
	return sellcode
'''

def trading_strategy(data,trading_pair,balance):
	indicators = indicator_data(data)
	
	global buyprice, buytime, buycomm, buysize, sell_cond_count, buy_order_finished, sell_order_finished
        

	# BUYING CONDITIONS
	
	if float(balance['xlm']) < 1: #no position
		if buy_order_finished == 1:
			if indicators[1]['EMA400'] > indicators[1]['EMA600']:
				if indicators[0]['EMA50'] < indicators[0]['EMA100'] and indicators[1]['EMA50'] > indicators[1]['EMA100']: #BEST
					buysize = float("{:.0f}".format(0.98*balance['usd']/data['close'].values[-1]))
					buyprice = float("{:.4f}".format(data['close'].values[-1]))
					#=======
					#BUY MSG
					#=======
					#self.buy(exectype=bt.Order.Limit,price=buyprice,size=buysize,valid=bt.date2num(data.num2date())+(5/1440))
					buy_order_finished = 0

	# SELLING CONDITIONS
	if float(balance['xlm']) >= 1: #position
		if sell_order_finished == 1:
			last_closing_price=data['close'].values[-1]
			
			if indicators[0]['EMA25'] > indicators[0]['EMA50'] and indicators[1]['EMA25'] < indicators[1]['EMA50']: #BEST
			
				if (last_closing_price > round(float("{:.6f}".format(buyprice + COMMRATE*(buyprice+last_closing_price))),4)):
					#========
					#SELL MSG
					#========
					'''
					lmt_1 = self.sell(
						exectype=bt.Order.Limit,
						price=last_closing_price,
						size=self.getposition().size,
						info= ('sellcode',1)
					)
					'''
					
					sell_order_finished = 0
	
	

	return indicators
	

def log_data(indicators,close_price,tick_time):#,order):
	
	"""
	if order.isbuy():
		type = "Buy " 
	elif order.issell():
		type = "Sell"
	else:
		type = "Report"

	if type != "Report":
		output = (f"Order: {order.ref:3d} Type: {type:<2} Status:" +
				 f" {order.getstatusname():1} ".ljust(12) +
				 f"Size: ".ljust(6) +
				 f"{order.created.size:6.0f} ".rjust(8)+
				 f"Price: {order.created.price:6.4f} ")
				 #f"Position: {self.getposition(order.data).size} " +
		if order.status in [order.Completed]:
			output += ( f"Cost: {order.executed.value:6.2f} " +
						f"Comm: {order.executed.comm:4.2f} " +
						f"RSI: {float(self.rsi[0]):3.2f} " 
						#f"EMA100: {float(self.EMA100[0]):3.2f} "
						)
			if not order.isbuy():
				output += ( f"net gain: {((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm):4.2f} " + 
							f"net gain %: {(100*(((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm)/(buysize*buyprice))):3.2f}% " +
							f"SC: {sellcount(list(order.info.items()))}"
							)
	"""
	output = (f"EMA600: {indicators[1]['EMA600']:6.4f} " +
				f"EMA400: {indicators[1]['EMA400']:6.4f} " +
				f"EMA100: {indicators[1]['EMA100']:6.4f} " +
				f"EMA50: {indicators[1]['EMA50']:6.4f} " +
				f"EMA25: {indicators[1]['EMA25']:6.4f} " +
				f"Close Price: {close_price:6.4f} "
				)
	dt = datetime.datetime.fromtimestamp(tick_time)

	print('%s, %s, %s' % (dt.date().isoformat(), dt.time(),output))

if __name__ == "__main__":

	loop = asyncio.get_event_loop()
	loop.run_until_complete(main())
