import backtrader as bt
import datetime
import numpy


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
sell_cond_count = [0,0,0,0,0,0]
min_close_last_x_periods = 0

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
                    f"RSI: {float(self.rsi[0]):3.2f} " 
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
        self.rsi = bt.talib.RSI(self.data, timeperiod=2)
        #self.rsi6 = bt.talib.RSI(self.data, timeperiod=6)
        #self.rsi100 = bt.talib.RSI(self.data, timeperiod=100)
        #self.rsi24 = bt.talib.RSI(self.data, timeperiod=24)
        
        self.macd = bt.talib.MACD(self.data,fastperiod=8, slowperiod=18, signalperiod=6)
        #self.EMA600 = bt.talib.EMA(self.data,timeperiod=600)
        #self.EMA400 = bt.talib.EMA(self.data,timeperiod=400)
        #self.SMA200 = bt.talib.SMA(self.data,timeperiod=200)
        #self.SMA100 = bt.talib.SMA(self.data,timeperiod=100)
        #self.SMA50 = bt.talib.SMA(self.data,timeperiod=50)
        #self.SMA25 = bt.talib.SMA(self.data,timeperiod=25)
        self.EMA20 = bt.talib.EMA(self.data,timeperiod=20)
        self.EMA100 = bt.talib.EMA(self.data,timeperiod=100)
        self.EMA50 = bt.talib.EMA(self.data,timeperiod=50)
        self.EMA25 = bt.talib.EMA(self.data,timeperiod=25)
        #self.SMA5 = bt.talib.SMA(self.data,timeperiod=5)
        bt.observers.BuySell(bardist=0)
        # To keep track of pending orders
        self.order = None

    def log(self, txt, dt=None):
        #Logging function for this strategy
        dt = dt or self.datas[0].datetime#.date(0)
        print('%s, %s, %s' % (dt.date(0).isoformat(), dt.time(),txt))

    def next(self):
        global buyprice, buytime, buycomm, buysize, sell_cond_count, buy_order_finished, sell_order_finished, min_close_last_x_periods
        
        # BUYING CONDITIONS
        if not self.position:
            if buy_order_finished == 1:
            #if data.close > 1.01*self.SMA200:
                if self.macd[-1] < self.macd.macdsignal[-1] and self.macd[0] > self.macd.macdsignal[0]:
                    #if self.EMA25[-1] > self.EMA50[-1]:# and self.SMA25[0] > self.SMA50[0]:
                    #if self.EMA50[-1] < self.EMA100[-1] and self.EMA50[0] > self.EMA100[0]: #BEST
                    if data.close[-1] < self.EMA20[-1] and data.close[0] > self.EMA20[0]:
                #if self.SMA5[-1] < self.SMA25[-1] and self.SMA5[0] > self.SMA25[0]:
                        #if self.rsi6 < 65: #and self.rsi[-1] < 15:# and self.rsi[0] < 25:
                           
                            #if abs((float(self.macd[0])-float(self.macd[-1]))/2) < 0.00001 :
                                
                            #if float(self.macd[0])-float(self.macd[-1]) < 0:
                                
                        buysize = float("{:.0f}".format(0.98*self.broker.get_cash()/data.close[0]))
                        buyprice = self.EMA20+0.01 #float("{:.4f}".format(data.close[0]))
                        min_close_last_x_periods = min(self.data.close.get(size=20))
                        self.buy(exectype=bt.Order.Limit,price=buyprice,size=buysize,valid=bt.date2num(data.num2date())+(5/1440))
                        buy_order_finished = 0
                        
    
        # SELLING CONDITIONS
        if self.position.size > 0:
            if sell_order_finished == 1:
                last_closing_price=data.close[0]
                
                #if self.SMA5[-2] > self.SMA25[-2] and self.SMA5[0] < self.SMA25[0]:
                #if self.SMA25[-1] > self.SMA50[-1] and self.SMA25[0] < self.SMA50[0]:
                if data.close[-1] > self.EMA50[-1] and last_closing_price < self.EMA50[0]:
                    #if self.rsi > 90: 
                    if (last_closing_price > round(float("{:.6f}".format(buyprice + COMMRATE*(buyprice+last_closing_price))),4)):
                        lmt_1 = self.sell(
                            exectype=bt.Order.Limit,
                            price=last_closing_price,
                            size=self.getposition().size,
                            info= ('sellcode',1)
                        )
                        sell_order_finished = 0
                
                elif bt.date2num(data.num2date()) > buytime+7:
                    if (last_closing_price > 0.95*round(float("{:.6f}".format(buyprice + COMMRATE*(buyprice+last_closing_price))),4)):
                        lmt_3 = self.sell(
                            exectype=bt.Order.Limit,
                            price=last_closing_price,
                            size=self.getposition().size,
                            info= ('sellcode',2)
                        )
                        sell_order_finished = 0
                
                '''   
                elif self.SMA400[-1] > self.SMA600[-1] and self.SMA400[0] < self.SMA600[0]: 
                        lmt_2 = self.sell(
                            exectype=bt.Order.Market,
                            size=self.getposition().size,
                            info= ('sellcode',3)
                        ) 
                        sell_order_finished = 0
                '''   
            
             
            
    
    def notify_order(self, order):
    
        # Check if an order has been completed
        # Attention: broker could reject order if not enough cash
        if order.status in [order.Completed]:
            global sell_order_finished, buyprice, buycomm, min_close_last_x_periods
            if order.isbuy():
                global buy_order_finished,buytime
                buycomm = order.executed.comm
                buytime = order.executed.dt
                buyprice = order.executed.price
                buy_order_finished = 1
                log_data(self,order)
                
                lmt_3 = self.sell(
                            exectype=bt.Order.Limit,
                            price=buyprice+buyprice-min_close_last_x_periods,
                            size=0.5*self.getposition().size,
                            info= ('sellcode',3)
                        )
                #sell_order_finished = 0
                lmt_4 = self.sell(
                            exectype=bt.Order.Limit,
                            price=min_close_last_x_periods,
                            size=self.getposition().size,
                            oco=lmt_3,
                            info= ('sellcode',4)
                        )
                sell_order_finished = 0
            else:
                #print(f'sellcode: {sellcount(list(order.info.items()))}, net gain: {((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm):6.2f}, net gain %: {(100*(((order.executed.price-buyprice)*buysize - order.executed.comm - buycomm)/(buysize*buyprice))):3.4f}%')
                global prev_max_balance, current_balance
                current_balance = self.broker.get_cash()
                if(current_balance > prev_max_balance):
                    prev_max_balance = current_balance
                sell_order_finished = 1
                log_data(self,order)
                if(list(order.info.items())[0][1][1] == 3):
                    lmt_5 = self.sell(
                        exectype=bt.Order.StopTrail,
                        price=max(self.EMA20,buyprice+2*COMMRATE),
                        trailamount=0.015,
                        size=self.getposition().size,
                        info= ('sellcode',5)
                    )
                    sell_order_finished = 0
                

        elif order.status in [order.Expired]:
            log_data(self,order)
            buy_order_finished = 1
        elif order.status in [order.Margin]:
            log_data(self,order)
            buy_order_finished = 1 
        else:
            log_data(self,order)

        self.order = None


cerebro = bt.Cerebro()

#fromdate = datetime.datetime.strptime('2020-01-01', '%Y-%m-%d')
#todate = datetime.datetime.strptime('2021-11-25', '%Y-%m-%d')
fromdate = datetime.datetime.strptime('2021-05-15', '%Y-%m-%d')
todate = datetime.datetime.strptime('2021-06-06', '%Y-%m-%d')
data = bt.feeds.GenericCSVData(dataname='data/2020-21_5m.csv', dtformat=2, compression=5, timeframe=bt.TimeFrame.Minutes, fromdate=fromdate, todate=todate)

cerebro.adddata(data)
initial_cash = 10000
cerebro.broker.set_cash(initial_cash)
cerebro.broker.setcommission(commission=COMMRATE)

cerebro.addstrategy(RSIStrategy)


cerebro.run()
print(f'Sell Condition Summary: 1: {sell_cond_count[0]}, 2: {sell_cond_count[1]}, 3: {sell_cond_count[2]}, 4: {sell_cond_count[3]}, 5: {sell_cond_count[3]}')
print(f'Initial Balance: {initial_cash:.2f}, Max Balance: {prev_max_balance:.2f}, End Balance: {current_balance:.2f}')#.format(initial_cash, prev_max_balance, cerebro.broker.get_cash()))
cerebro.plot()