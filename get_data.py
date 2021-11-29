import config, csv
from binance.client import Client

#   client = Client(config.API_KEY, config.API_SECRET)
try:
    #tld='' is used to denote which server to use (.com or .us)
    client = Client(config.API_KEY, config.API_SECRET,{"timeout": 6},tld='us')
    print("Client Initialized Successfully")
except requests.exceptions.RequestException as e:
    print("Connection error - Initializing API client")
    sys.exit()

# prices = client.get_all_tickers()

# for price in prices:
#     print(price)

csvfile = open('data/2020-21_5m.csv', 'w', newline='') 
candlestick_writer = csv.writer(csvfile, delimiter=',')

candlesticks = client.get_historical_klines("XLMUSD", Client.KLINE_INTERVAL_5MINUTE, "1 Jan, 2020", "8 Nov, 2021")
#candlesticks = client.get_historical_klines("BTCUSDT", Client.KLINE_INTERVAL_1DAY, "1 Jan, 2020", "12 Jul, 2020")
#candlesticks = client.get_historical_klines("BTCUSDT", Client.KLINE_INTERVAL_1DAY, "1 Jan, 2017", "12 Jul, 2020")

for candlestick in  candlesticks:
    candlestick[0] = candlestick[0] / 1000
    candlestick_writer.writerow(candlestick)

csvfile.close()
