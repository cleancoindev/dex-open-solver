from .order_util import IntegerTraits
from .order import Order
from typing import List, Dict, Tuple
from fractions import Fraction as F


def compute_objective_values(prices, accounts_updated, orders, fee):
    """Compute objective function values."""
    # Init objective values.
    obj = {'volume': 0,
           'utility': 0,
           'utility_disreg': 0,
           'utility_disreg_touched': 0,
           'fees': 0,
           'orders_touched': 0}

    for order in orders:
        if prices[order.buy_token] is None or prices[order.sell_token] is None:
            assert order.buy_amount == 0 and order.sell_amount == 0
            continue
        else:
            sell_token_price = prices[order.sell_token]
            buy_token_price = prices[order.buy_token]

        # Volume (referring to sell amount).
        obj['volume'] += order.sell_amount * sell_token_price

        xrate = F(buy_token_price, sell_token_price)

        # Utility at current prices.
        u = IntegerTraits.compute_utility_term(
            order=order,
            xrate=xrate,
            buy_token_price=buy_token_price,
            fee=fee
        )

        # Compute maximum possible utility analogously to the smart contract
        # (i.e., depending on the remaining token balance after order execution).
        if order.account_id is not None:
            balance_updated = accounts_updated[order.account_id].get(order.sell_token, 0)
        else:
            balance_updated = 0
        max_sell_amount_ = min(order.max_sell_amount, order.sell_amount + balance_updated)
        umax = IntegerTraits.compute_max_utility_term(
            order=order.with_max_sell_amount(max_sell_amount_),
            xrate=xrate,
            buy_token_price=buy_token_price,
            fee=fee
        )

        # TODO: can this happen here?
        # if u > umax:
        #    logger.warning(
        #        "Computed utility of <%s> larger than maximum utility!\n"
        #        "u    = %d\n"
        #        "umax = %d"
        #        % (oID, u, umax))

        obj['utility'] += u
        obj['utility_disreg'] += max([(umax - u), 0])

        if order.sell_amount > 0:
            obj['orders_touched'] += 1
            obj['utility_disreg_touched'] += (umax - u)

            order.utility = u
            order.utility_disreg = (umax - u)

        # Fee amount as net difference of fee token sold/bought.
        if order.sell_token == fee.token:
            obj['fees'] += order.sell_amount
        elif order.buy_token == fee.token:
            obj['fees'] -= order.buy_amount

    return obj


def filter_orders_tokenpair(
    orders: List[Order],
    token_pair: Tuple[str, str]
) -> List[Dict]:
    """Find all orders on a single given token pair.

    Args:
        orders: List of orders.
        tokenpair: Tuple of two token IDs.

    Returns:
        The filtered orders.

    """
    return [
        order for order in orders
        if set(token_pair) == {order.sell_token, order.buy_token}
    ]


def restrict_order_sell_amounts_by_balances(
    orders: List[Order],
    accounts: Dict[str, Dict[str, int]]
) -> List[Dict]:
    """Restrict order sell amounts to available account balances.

    This method also filters out orders that end up with a sell amount of zero.

    Args:
        orders: List of orders.
        accounts: Dict of accounts and their token balances.

    Returns:
        The capped orders.

    """
    orders_capped = []

    # Init dict for remaining balance per account and token pair.
    remaining_balances = {}

    # Iterate over orders sorted by limit price (best -> worse).
    for order in sorted(orders, key=lambda o: o.max_xrate, reverse=True):
        aID, tS, tB = order.account_id, order.sell_token, order.buy_token

        # Init remaining balance for new token pair on some account.
        if (aID, tS, tB) not in remaining_balances:
            sell_token_balance = F(accounts.get(aID, {}).get(tS, 0))
            remaining_balances[(aID, tS, tB)] = sell_token_balance

        # Get sell amount (capped by available account balance).
        sell_amount_old = order.max_sell_amount
        sell_amount_new = min(sell_amount_old, remaining_balances[aID, tS, tB])

        # Skip orders with zero sell amount.
        if sell_amount_new == 0:
            continue
        else:
            assert sell_amount_old > 0

        # Update remaining balance.
        remaining_balances[aID, tS, tB] -= sell_amount_new
        assert remaining_balances[aID, tS, tB] >= 0

        order.max_sell_amount = sell_amount_new

        # Append capped order.
        orders_capped.append(order)

    return orders_capped
