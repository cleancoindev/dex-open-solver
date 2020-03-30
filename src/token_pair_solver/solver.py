import json
import logging
from decimal import Decimal as D
from fractions import Fraction as F

from src.core.api import dump_solution
from src.core.constants import FEE_TOKEN_PRICE
from src.core.round import round_solution
from src.core.validation import validate

from .amount import compute_buy_amounts
from .api import load_problem
from .orderbook import (IntegerTraits, RationalTraits,
                        compute_b_buy_token_imbalance)
from .price import compute_token_price_to_cover_imbalance, create_market_order
from .xrate import find_best_xrate

logger = logging.getLogger(__name__)


def solve_token_pair(
    token_pair, b_orders, s_orders, fee,
    xrate=None, b_buy_token_price=None
):
    assert len(b_orders) > 0 and len(s_orders) > 0

    b_buy_token, s_buy_token = token_pair

    if b_buy_token == fee.token:
        b_buy_token_price = FEE_TOKEN_PRICE

    # Compute optimal exchange rate if not given.
    if xrate is None:
        xrate, _ = find_best_xrate(b_orders, s_orders, fee)
        logger.debug(
            "p(%s) / p(%s) = %s (precise arithmetic)",
            b_buy_token,
            s_buy_token, xrate
        )

    # Return if there is no possible order matching.
    if xrate is None:
        return None

    # If b_buy_token_price is given, adjust xrate so that
    # xrate = b_buy_token_price / s_buy_token_price
    # and s_buy_token_price is an integer.
    if b_buy_token_price is not None:
        s_buy_token_price = round(b_buy_token_price / xrate)
        xrate = F(b_buy_token_price, s_buy_token_price)
        logger.debug("Adjusted xrate\t:\t%s", xrate)

    # Execute orders based on optimal exchange rate.
    compute_buy_amounts(xrate, b_orders, s_orders, fee)

    return xrate


def solve_token_pair_and_fee_token(
    token_pair, accounts, b_orders, s_orders, f_orders, fee, xrate
):
    b_buy_token, s_buy_token = token_pair

    logger.debug("=== Order matching on token pair + fee token ===")
    logger.debug("b_buy_token\t:\t%s", b_buy_token)
    logger.debug("s_buy_token\t:\t%s", s_buy_token)
    logger.debug("fee_token  \t:\t%s", fee.token)

    # Find optimal execution between b_buy_token <-> s_buy_token.
    logger.debug("")
    logger.debug(
        "=== Matching %s -- %s (rational arithmetic) ===",
        b_buy_token, s_buy_token
    )
    xrate = solve_token_pair(token_pair, b_orders, s_orders, fee, xrate=xrate)

    if xrate is None:
        logger.info("No matching orders between %s and %s.", b_buy_token, s_buy_token)
        return [], {}

    if b_buy_token == fee.token:
        # If b_buy_token is fee, then there is only two sets of orders,
        # b_orders, and s_orders, for which buy_amounts were computed above.
        f_orders = []
        b_buy_token_price = FEE_TOKEN_PRICE
    else:
        # Otherwise orders buying b_buy_token for fee must be considered, so that
        # the b_buy_token imbalance due to fee and rounding can be bought.
        assert len(f_orders) > 0

        logger.debug("")
        logger.debug("=== Computing price of %s ===", b_buy_token)

        # Find imbalance due to fee of b_buy_token
        # (imbalance due to fee of s_buy_token is zero).
        b_buy_token_imbalance = compute_b_buy_token_imbalance(
            b_orders, s_orders,
            xrate, 1, fee, RationalTraits
        )
        logger.debug(
            "Imbalance of %s\t:\t%s (due to fee)", b_buy_token, b_buy_token_imbalance
        )

        # Compute b_buy_token_price such that it is possible to sell the b_buy_token
        # imbalance due to fee (plus a rounding buffer) for fee.
        # This fixes the final b_buy_token_price.
        b_buy_token_price = compute_token_price_to_cover_imbalance(
            buy_token=b_buy_token,
            fee=fee,
            buy_token_imbalance=b_buy_token_imbalance,
            f_orders=f_orders
        )
        logger.debug("Price of %s\t:\t%s", b_buy_token, b_buy_token_price)

        logger.debug("")
        logger.debug(
            "=== (Re)matching %s -- %s (with fixed price for %s) ===",
            b_buy_token, s_buy_token, b_buy_token
        )

        # Re-execute orders between token pair with the fixed b_buy_token_price.
        # This fixes the final xrate, and therefore the final s_buy_token_price.
        xrate = solve_token_pair(
            token_pair, b_orders, s_orders, fee,
            xrate=xrate, b_buy_token_price=b_buy_token_price
        )
        logger.debug("Price of %s\t:\t%s", s_buy_token, b_buy_token_price / xrate)
        logger.debug(
            "Amounts of %s bought in exchange for %s:",
            b_buy_token, s_buy_token
        )
        logger.debug("\t%s", [b_order.buy_amount for b_order in b_orders])
        logger.debug(
            "Amounts of %s bought in exchange for %s:",
            s_buy_token, b_buy_token
        )
        logger.debug("\t%s", [s_order.buy_amount for s_order in s_orders])

        logger.debug("")
        logger.debug(
            "=== Executing orders buying b_buy_token (%s) for FEE (%s) ===",
            b_buy_token, fee.token
        )

        # Execute orders that buy the b_buy_token imbalance due to fee for fee.
        # 1/2: create an artificial order selling the b_buy_token imbalance for fee.
        fee_debt_order = create_market_order(
            buy_token=fee.token, sell_token=b_buy_token,
            sell_amount=b_buy_token_imbalance,
            s_orders=f_orders
        )

        # 2/2: execute the artifical order against existing orders buying b_buy_token
        # for fee (i.e. f_orders).
        fee_xrate = F(FEE_TOKEN_PRICE, b_buy_token_price)
        logger.debug(
            "Exchange rate p(%s) / p(%s):\t%s",
            fee.token, b_buy_token, fee_xrate
        )
        fee_xrate = solve_token_pair(
            (fee.token, b_buy_token),
            [fee_debt_order], f_orders, fee,
            xrate=fee_xrate
        )
        assert fee_xrate is not None

        logger.debug(
            "Amounts of %s bought in exchange for FEE (%s):",
            b_buy_token, fee.token
        )
        logger.debug("\t%s", [f_order.buy_amount for f_order in f_orders])

    # Aggregate orders.
    orders = b_orders + s_orders + f_orders

    # Aggregate prices.
    prices = {
        fee.token: FEE_TOKEN_PRICE,
        b_buy_token: b_buy_token_price,
        s_buy_token: b_buy_token_price / xrate
    }

    # Integrate sell_amounts and prices in solution, and round.
    logger.debug("")
    logger.debug("=== Rounding ===")
    if not round_solution(prices, orders, fee):
        logger.warning("Could not round solution.")
        return [], {}

    # Make sure the solution is correct.
    validate(accounts, orders, prices, fee)

    return orders, prices


def main(args):
    # Load dict from json.
    instance = json.load(args.instance, parse_float=D)

    # Load problem.
    # b_orders: orders buying b_buy_token
    # s_orders: orders selling b_buy_token (buying s_buy_token)
    # f_orders: orders selling fee token for b_buy_token
    accounts, b_orders, s_orders, f_orders, fee = load_problem(
        instance, args.token_pair
    )

    # Find token pair + fee token matching.
    orders, prices = solve_token_pair_and_fee_token(
        args.token_pair, accounts, b_orders, s_orders, f_orders, fee, xrate=args.xrate
    )

    # Dump solution to file.
    dump_solution(
        instance, args.solution,
        orders,
        prices,
        fee=fee,
        arith_traits=IntegerTraits()
    )

    logger.info("Solution file is '%s'.", args.solution.name)


def setup_arg_parser(subparsers):
    parser = subparsers.add_parser(
        'token-pair', help="Matches orders on a given token pair."
    )

    parser.add_argument(
        'token_pair',
        type=str,
        nargs=2,
        help='Token pair (b_buy_token, s_buy_token).'
    )
    parser.add_argument(
        '--xrate',
        type=F,
        help='Exchange rate (token1/token2) as a fraction.'
    )

    parser.set_defaults(exec_subcommand=main)