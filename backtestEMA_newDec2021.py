import backtrader as bt
import datetime
import numpy
import pandas as pd
from numpy import diff


######################
# Bollinger Bands + RSI Strategy - Best Performing Overall
#
# Makes good returns AND deals with crashes - better overall returns when you 
# dump after a week or so then keep trading rather than wait for price to recover
#
# Buy and hold - 10x max from jan 1 2020 to dec 2021
# This strat -   12.6x
#
# Buy and hold - 5x max, 2x normal from jan 1 2021 to dec 2021
# This strat -   9x max, 7.5x total
#####################

# TALib MA_TYPE List for Reference:
# 0: Simple_Moving_Average # 2nd best at 73k
# 1: Exponential_Moving_Average
# 2: Weighted_Moving_Average
# 3: Double_Exponential_Moving_Average
# 4: Triple_Exponential_Moving_Average
# 5: Triangular_Moving_Average BEST SO FAR AT 79k
# 6: Kaufman_Adaptive_Moving_Average
# 7: MESA_Adaptive_Moving_Average
# 8: Triple_Generalized_Double_Exponential

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

def sellcount(orderinfo):
	sellcode=orderinfo[0][1][1]
	global sell_cond_count
	sell_cond_count[sellcode-1] += 1
	return sellcode

def log_data(self,order):
	type = "Buy " if order.isbuy() else "Sell"
	output = (f"Order: {order.ref:3d} Type: {type:<2} Status:" +
			 f" {order.getstatusname():1} ".ljust(12) +
			 f"Size: ".ljust(6) +
			 f"{order.created.size:6.0f} ".rjust(8)+
			 f"Price: {order.created.price:6.4f} ")
			 #f"Position: {self.getposition(order.data).size} " +
	if order.status in [order.Completed]:
		output += ( f"Cost: {order.executed.value:6.2f} " +
					f"Comm: {order.executed.comm:4.2f} " +
					f"RSI: {float(self.rsi6[0]):3.1f} " 
					#f"EMA100: {float(self.EMA100[0]):3.2f} "
					)
		if not order.isbuy():
			output += ( f"net gain: {((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm):4.2f} " + 
						f"net gain %: {(100*(((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm)/(buysize*buyprice))):3.2f}% " +
						f"SC: {sellcount(list(order.info.items()))}"
						)
	self.log(output)
	

class RSIStrategy(bt.Strategy):

	def __init__(self):
		#self.rsi = bt.talib.RSI(self.data, timeperiod=2)
		self.rsi20 = bt.talib.RSI(self.data, timeperiod=20)
		#self.rsi100 = bt.talib.RSI(self.data, timeperiod=100)
		self.rsi6 = bt.talib.RSI(self.data, timeperiod=6)
		self.rsi50 = bt.talib.RSI(self.data, timeperiod=50)
		self.rsi12 = bt.talib.RSI(self.data, timeperiod=12)
		
		#self.macd = bt.talib.MACD(self.data)
		self.EMA600 = bt.talib.EMA(self.data,timeperiod=600)
		self.EMA400 = bt.talib.EMA(self.data,timeperiod=400)
		#self.SMA200 = bt.talib.SMA(self.data,timeperiod=200)
		#self.SMA100 = bt.talib.SMA(self.data,timeperiod=100)
		self.bband = bt.talib.BBANDS(self.data,timeperiod=20,nbdevup=2.0,nbdevdn=2.0,matype=5)
		#self.bband = bt.indicators.BBands(self.datas[0], period=20)
		#self.MACD = bt.talib.MACD(self.data,fastperiod=12,slowperiod=26,signalperiod=9)

		#self.EMA100 = bt.talib.EMA(self.data,timeperiod=100)
		#self.EMA50 = bt.talib.EMA(self.data,timeperiod=50)
		#self.EMA25 = bt.talib.EMA(self.data,timeperiod=25)
		#self.EMA15 = bt.talib.EMA(self.data,timeperiod=15)
		#self.EMA7 = bt.talib.EMA(self.data,timeperiod=7)
		bt.observers.BuySell(bardist=0)
		# To keep track of pending orders
		self.order = None

	def log(self, txt, dt=None):
		#Logging function for this strategy
		dt = dt or self.datas[0].datetime
		print('%s, %s, %s' % (dt.date(0).isoformat(), dt.time(),txt))

	def next(self):
		global buyprice, buytime, buycomm, buysize, sell_cond_count, buy_order_finished, sell_order_finished
		
		# BUYING CONDITIONS
		if not self.position:
			if buy_order_finished == 1:
				if data.low[0] < 0.99*self.bband.lines.lowerband:
					if self.rsi6[0] < 31.25:
						#if self.MACD[-1] > self.MACD[0] and abs(diff(self.MACD)[0]) < 0.0001:	
						buysize = float("{:.0f}".format(0.98*self.broker.get_cash()/(data.close[0])))
						buyprice = float("{:.4f}".format(data.close[0]))
						#self.buy(exectype=bt.Order.Limit,price=buyprice,size=buysize,valid=bt.date2num(data.num2date())+(120/1440))
						self.buy(exectype=bt.Order.Market,size=buysize,valid=bt.date2num(data.num2date())+(120/1440))
						buy_order_finished = 0
		#LEVERS
		# buy rsiX, buy rsi < X, buy X*self.bband.lines.bot, sell rsiX, sell rsi > X, sell stop buytime+X days

		# SELLING CONDITIONS
		if self.position.size > 0:
			if sell_order_finished == 1:
				if data.high[0] >= self.bband.lines.upperband or self.rsi50[0] > 70:
					#if self.MACD[-1] < self.MACD[0] and abs(diff(self.MACD)[0]) < 0.0001:
					#if data.close[0] > buyprice:
					if (data.close[0] > round(float("{:.6f}".format(buyprice + COMMRATE*(buyprice+data.close[0]))),4)):
						
						lmt_1 = self.sell(
							exectype=bt.Order.Market,
							#price=last_closing_price,
							size=self.getposition().size,
							info= ('sellcode',1)
						)
						sell_order_finished = 0
				elif self.data.datetime.get()[0] > buytime+8.5:
					lmt_2 = self.sell(
						exectype=bt.Order.Market,
						#price=last_closing_price,
						size=self.getposition().size,
						info= ('sellcode',2)
					)
					sell_order_finished = 0
			"""
			elif data.close[0] < 0.7*buyprice:
				lmt_2 = self.sell(
						exectype=bt.Order.Market,
						#price=last_closing_price,
						size=self.getposition().size,
						info= ('sellcode',2)
					)
				sell_order_finished = 0
			"""
	def notify_order(self, order):
	
		# Check if an order has been completed
		# Attention: broker could reject order if not enough cash
		if order.status in [order.Completed]:
			
			if order.isbuy():
				global buycomm,buy_order_finished,buytime
				buycomm = order.executed.comm
				buytime = order.executed.dt
				buy_order_finished = 1
				log_data(self,order)

			else:
				#print(f'sellcode: {sellcount(list(order.info.items()))}, net gain: {((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm):6.2f}, net gain %: {(100*(((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm)/(buysize*buyprice))):3.4f}%')
				global prev_max_balance, current_balance,sell_order_finished
				current_balance = self.broker.get_cash()
				if(current_balance > prev_max_balance):
					prev_max_balance = current_balance
				sell_order_finished = 1
				log_data(self,order)
		elif order.status in [order.Expired, order.Margin]:
			log_data(self,order)
			buy_order_finished = 1       
		else:
			log_data(self,order)

		self.order = None


cerebro = bt.Cerebro()

#fromdate = datetime.datetime.strptime('2020-01-01', '%Y-%m-%d')
#todate = datetime.datetime.strptime('2021-11-25', '%Y-%m-%d')
fromdate = datetime.datetime.strptime('2021-01-01', '%Y-%m-%d')
todate = datetime.datetime.strptime('2021-12-12', '%Y-%m-%d')
data = bt.feeds.GenericCSVData(dataname='data/2020-21_1h.csv', dtformat=2, compression=60, timeframe=bt.TimeFrame.Minutes, fromdate=fromdate, todate=todate)

cerebro.adddata(data)
initial_cash = 10000
cerebro.broker.set_cash(initial_cash)
cerebro.broker.setcommission(commission=COMMRATE)

cerebro.addstrategy(RSIStrategy)


cerebro.run()
print(f'Sell Condition Summary: 1: {sell_cond_count[0]}, 2: {sell_cond_count[1]}, 3: {sell_cond_count[2]}, 4: {sell_cond_count[3]}')
print(f'Initial Balance: {initial_cash:.2f}, Max Balance: {prev_max_balance:.2f}, End Balance: {current_balance:.2f}')#.format(initial_cash, prev_max_balance, cerebro.broker.get_cash()))
#cerebro.plot()