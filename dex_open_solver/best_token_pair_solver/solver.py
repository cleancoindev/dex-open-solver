import json
import logging
import time
from copy import deepcopy
from decimal import Decimal as D
from functools import reduce
from random import shuffle

from ..core.api import IntegerTraits, Stats, dump_solution, load_problem
from ..core.orderbook import compute_objective, update_accounts
from ..token_pair_solver.solver import \
    solve_token_pair_and_fee_token_economic_viable

logger = logging.getLogger(__name__)


TRIVIAL_SOLUTION = ([], {})


def match_token_pair(token_pair, accounts, orders, fee):
    b_buy_token, s_buy_token = token_pair

    b_orders = [
        order for order in orders
        if order.buy_token == b_buy_token and order.sell_token == s_buy_token
    ]
    if len(b_orders) == 0:
        return TRIVIAL_SOLUTION

    s_orders = [
        order for order in orders
        if order.buy_token == s_buy_token and order.sell_token == b_buy_token
    ]

    if len(s_orders) == 0:
        return TRIVIAL_SOLUTION

    if b_buy_token != fee.token:
        f_orders = [
            order for order in orders
            if order.buy_token == b_buy_token and order.sell_token == fee.token
        ]
        if len(f_orders) == 0:
            return TRIVIAL_SOLUTION
    else:
        f_orders = []

    # Find token pair + fee token matching.
    orders, prices = solve_token_pair_and_fee_token_economic_viable(
        token_pair, accounts, b_orders, s_orders, f_orders, fee
    )
    return (orders, prices)


def match_token_pair_and_evaluate(
    token_pair, accounts, orders, fee, touched_only=False
):
    """If touched_only=true, then evaluate objective over touched orders only."""

    # Compute current token pair solution: buy/sell amounts and best prices.
    orders, prices = match_token_pair(token_pair, accounts, orders, fee)

    # Update accounts for current token pair solution.
    accounts_updated = deepcopy(accounts)
    update_accounts(accounts_updated, orders)

    # Compute objective value for current token pair solution.
    if touched_only:
        touched_orders = [o for o in orders if o.buy_amount > 0]
        objective = compute_objective(prices, accounts_updated, touched_orders, fee)
    else:
        objective = compute_objective(prices, accounts_updated, orders, fee)

    return (objective, (orders, prices))


def eligible_token_pairs(orders, fee_token):
    # Set of tokens directly connected to fee token (except fee).
    directly_connected_tokens = {
        o.buy_token for o in orders if o.sell_token == fee_token
    }

    # A set with all tokens.
    all_tokens = reduce(lambda x, y: x | y, (o.tokens for o in orders), set())

    # All permutations that do not include fee, and where the first
    # token in the token pair is directly connected to fee.
    for b_token in directly_connected_tokens:
        for s_token in all_tokens - {b_token, fee_token}:
            yield (b_token, s_token)

    # All permutations where the first token is the fee token.
    for s_token in all_tokens - {fee_token}:
        yield (fee_token, s_token)


def main(args):
    start_time = time.time()

    # Load dict from json.
    instance = json.load(args.instance, parse_float=D)

    # Load problem.
    accounts, orders, fee = load_problem(instance)

    # Find token pair + fee token matching.
    # TODO: parallelize this loop.
    best_objective = 0
    best_solution = TRIVIAL_SOLUTION

    # Shuffle token pairs so that the open solver has a chance
    # to solve an instance in consecutive batches in the
    # case the timeout is limiting each run to complete.
    token_pairs = list(eligible_token_pairs(orders, fee.token))
    shuffle(token_pairs)
    for token_pair in token_pairs:
        objective, solution = match_token_pair_and_evaluate(
            token_pair, accounts, orders, fee, touched_only=True
        )
        if best_objective is None or objective > best_objective:
            best_objective = objective
            best_solution = deepcopy(solution)
        if hasattr(args, 'time_limit') and \
           args.time_limit is not None and \
           args.time_limit < time.time() - start_time:
            logging.warning("Time limit reached - leaving.")
            break

    orders, prices = best_solution

    runtime = time.time() - start_time
    stats = Stats(runtime=runtime, exit_status="completed")

    # Dump solution to file.
    dump_solution(
        instance, args.solution_filename,
        orders,
        prices,
        fee=fee,
        stats=stats,
        arith_traits=IntegerTraits
    )

    return instance


def setup_arg_parser(subparsers):
    parser = subparsers.add_parser(
        'best-token-pair',
        help="Matches orders on the token pair that leads to higher objective."
    )

    parser.set_defaults(exec_subcommand=main)
