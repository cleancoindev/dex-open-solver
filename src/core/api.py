import json
import logging
import tempfile
from collections import namedtuple
from copy import deepcopy
from fractions import Fraction as F

from .order import Order
from .order_util import IntegerTraits
from .orderbook import (compute_solution_metrics,
                        restrict_order_sell_amounts_by_balances,
                        update_accounts)
from .util import stringify_numeric

logger = logging.getLogger(__name__)

Fee = namedtuple('Fee', ['token', 'value'])


def load_fee(fee_dict):
    return Fee(token=fee_dict['token'], value=F(fee_dict['ratio']))


def load_problem(instance):
    """Load and setup a problem from an instance json."""
    accounts = deepcopy(instance['accounts'])

    orders = [
        Order.load_from_dict(order_dict, str(index))
        for index, order_dict in enumerate(instance['orders'])
    ]

    orders = restrict_order_sell_amounts_by_balances(orders, accounts)

    fee = load_fee(instance['fee'])

    return accounts, orders, fee


def dump_solution(
    instance,
    solution_filename,
    orders,
    prices,
    fee,
    arith_traits=IntegerTraits
):
    # Dump prices.
    instance['prices'] = prices

    # Update accounts.
    accounts = instance['accounts']
    update_accounts(accounts, orders)

    # Dump objective info.
    instance['objVals'] = compute_solution_metrics(prices, accounts, orders, fee)

    # Dump touched orders.
    orders = sorted(orders, key=lambda order: order.id)
    orders_ids = {order.id for order in orders}
    original_orders = [
        order for index, order in enumerate(instance['orders'])
        if str(index) in orders_ids
    ]
    touched_orders = []
    for order, original_order in zip(orders, original_orders):
        if order.sell_amount > 0:
            original_order['execSellAmount'] = str(order.sell_amount)
            original_order['execBuyAmount'] = str(order.buy_amount)
            touched_orders.append(original_order)
    instance['orders'] = touched_orders

    # Restore fee as a float (is Decimal).
    instance['fee']['ratio'] = float(instance['fee']['ratio'])

    # Dump json.
    instance = stringify_numeric(instance)
    for order in instance['orders']:
        if 'orderID' in order.keys():
            order['orderID'] = int(order['orderID'])
    if solution_filename is None:
        solution_file = tempfile.NamedTemporaryFile(
            mode='w+', delete=False, prefix='solution-', suffix='.json'
        )
        solution_filename = solution_file.name
    else:
        solution_file = open(solution_filename, "w+")
    json.dump(instance, solution_file, indent=4)
    solution_file.close()

    logger.info("Solution file is '%s'.", solution_filename)
