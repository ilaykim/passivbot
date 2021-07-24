import asyncio
import datetime
import json
from typing import Tuple, List

import aiohttp
import websockets

from bots.base_bot import Bot, ORDER_UPDATE, ACCOUNT_UPDATE
from definitions.candle import Candle, empty_candle_list
from definitions.order import Order, empty_order_list
from definitions.position import Position
from functions import load_key_secret, print_


class LiveBot(Bot):
    def __init__(self, config: dict, strategy):
        super(LiveBot, self).__init__()
        self.config = config
        self.strategy = strategy

        self.symbol = config['symbol']

        self.user = config['user']

        self.session = aiohttp.ClientSession()

        self.key, self.secret = load_key_secret(config['exchange'], self.user)

        self.call_interval = config['call_interval']

        self.base_endpoint = ''
        self.endpoints = {
            'listenkey': '',
            'position': '',
            'balance': '',
            'exchange_info': '',
            'leverage_bracket': '',
            'open_orders': '',
            'ticker': '',
            'fills': '',
            'income': '',
            'create_order': '',
            'cancel_order': '',
            'ticks': '',
            'margin_type': '',
            'leverage': '',
            'position_side': '',
            'websocket': '',
            'websocket_user': ''
        }

    async def async_init(self):
        self.init()
        pass

    async def fetch_orders(self) -> List[Order]:
        raise NotImplementedError

    async def fetch_position(self) -> Tuple[Position, Position]:
        raise NotImplementedError

    async def fetch_balance(self) -> float:
        raise NotImplementedError

    async def public_get(self, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def private_(self, type_: str, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def private_get(self, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def private_post(self, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def private_put(self, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def private_delete(self, url: str, params: dict = {}) -> dict:
        raise NotImplementedError

    async def update_heartbeat(self):
        pass

    async def async_reset(self):
        self.reset()
        await self.async_init_orders()
        await self.async_init_position()
        await self.async_init_balance()
        self.strategy.update_values(self.get_balance(), self.get_position(), self.get_orders())

    async def async_init_orders(self):
        self.init_orders()
        a = await self.fetch_orders()
        add_orders = empty_order_list()
        delete_orders = empty_order_list()
        for order in a:
            add_orders.append(order)
        self.update_orders(add_orders, delete_orders)

    async def async_init_position(self):
        self.init_orders()
        long, short = await self.fetch_position()
        self.update_position(long, short)

    async def async_init_balance(self):
        self.init_balance()
        bal = await self.fetch_balance()
        self.update_balance(bal)

    async def async_handle_order_update(self, msg):
        self.handle_order_update(self.prepare_order(msg))
        if self.position_change and self.order_fill_change:
            asyncio.create_task(self.async_execute_strategy_update())

    async def async_handle_account_update(self, msg):
        self.handle_account_update(*self.prepare_account(msg))
        if self.position_change and self.order_fill_change:
            asyncio.create_task(self.async_execute_strategy_update())

    async def start_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.update_heartbeat()

    async def start_user_data(self) -> None:
        while True:
            try:
                self.position_change = False
                self.order_fill_change = False
                await self.async_reset()
                await self.update_heartbeat()
                async with websockets.connect(self.endpoints['websocket_user']) as ws:
                    async for msg in ws:
                        if msg is None:
                            continue
                        try:
                            msg = json.loads(msg)
                            type = self.determine_update_type(msg)
                            if type:
                                # print(msg)
                                if type == ORDER_UPDATE:
                                    asyncio.create_task(self.async_handle_order_update(msg))
                                elif type == ACCOUNT_UPDATE:
                                    asyncio.create_task(self.async_handle_account_update(msg))
                        except Exception as e:
                            print_(['User stream error inner', e], n=True)
            except Exception as e_out:
                print_(['User stream error outer', e_out], n=True)
                print_(['Retrying to connect in 5 seconds...'], n=True)
                await asyncio.sleep(5)

    async def start_websocket(self) -> None:
        while True:
            price_list = empty_candle_list()
            last_update = datetime.datetime.now()
            last_candle = Candle(0.0, 0.0, 0.0, 0.0, 0.0)
            async with websockets.connect(self.endpoints['websocket'] + f"{self.symbol.lower()}@kline_1m") as ws:
                async for msg in ws:
                    if msg is None:
                        continue
                    try:
                        msg = json.loads(msg)
                        candle = self.prepare_candle(msg, last_candle)
                        price_list.append(candle)
                        last_candle = candle
                        current = datetime.datetime.now()
                        if current - last_update >= datetime.timedelta(seconds=self.strategy.call_interval):
                            last_update = current
                            print_(['Do something'], n=True)
                            # asyncio.create_task(self.async_decide(price_list))
                            price_list = empty_candle_list()
                    except Exception as e:
                        if 'success' not in msg:
                            print_(['Error in price stream', e, msg], n=True)

    async def execute_leverage_change(self):
        raise NotImplementedError

    async def async_create_orders(self, orders_to_create: List[Order]):
        raise NotImplementedError

    async def async_cancel_orders(self, orders_to_cancel: List[Order]):
        raise NotImplementedError

    async def async_execute_strategy_update(self):
        add_orders, delete_orders = self.execute_strategy_update()

        asyncio.create_task(self.async_cancel_orders(delete_orders))
        asyncio.create_task(self.async_create_orders(add_orders))

    async def async_decide(self, prices: List[Candle]):
        add_orders, delete_orders = self.decide(prices)

        await self.async_cancel_orders(delete_orders)
        await self.async_create_orders(add_orders)
