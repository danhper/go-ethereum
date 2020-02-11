import argparse
import logging
import gzip
import json
import datetime as dt
from collections import defaultdict
import copy
from os import path

import matplotlib.pyplot as plt

import web3

# contract: https://etherscan.io/address/0x9ef05f7f6deb616fd37ac3c959a2ddd25a54e4f5


def get_func_signature_hash(signature):
    return web3.Web3.keccak(text=signature).hex()[2:2 + FUNC_LEN]


FUNC_LEN = 8
FUNC_HASHES = dict(
    vote=get_func_signature_hash("vote(address[])"),
    vote_slate=get_func_signature_hash("vote(bytes32)"),
    etch=get_func_signature_hash("etch(address[])"),
    lift=get_func_signature_hash("lift(address)"),
    lock=get_func_signature_hash("lock(uint256)"),
    free=get_func_signature_hash("free(uint256)"),
)

STR_WORD_SIZE = 64
STR_ADDRESS_SIZE = 40

DATA_DIR = path.realpath("data")


def load_data(data_dir=DATA_DIR):
    with gzip.open(path.join(data_dir, "mkr-governance-txs.json.gz")) as f:
        return json.load(f)


def is_call(tx, func_name):
    prefix = "0x" + FUNC_HASHES[func_name]
    return tx["input"].startswith(prefix)


def get_vote_addresses(tx):
    args = tx["input"][FUNC_LEN + 2:]
    args_count = int(args[STR_WORD_SIZE:STR_WORD_SIZE * 2], 16)
    if not args_count:
        return ()
    args_start = STR_WORD_SIZE * 2
    args_end = args_start + STR_WORD_SIZE * args_count
    addr_start = STR_WORD_SIZE - STR_ADDRESS_SIZE
    addresses = [args[i:i + STR_WORD_SIZE][addr_start:]
                 for i in range(args_start, args_end, STR_WORD_SIZE)]
    return tuple(addresses)


def get_lift_address(tx):
    return tx["input"][FUNC_LEN + 2:][STR_WORD_SIZE - STR_ADDRESS_SIZE:]


def get_slate(tx):
    return "0x" + tx["input"][FUNC_LEN + 2:]


def get_locked_amount(tx):
    return int(tx["input"][FUNC_LEN + 2:], 16) / 1e18


def get_time(tx):
    return dt.datetime.fromtimestamp(int(tx["timeStamp"]))


def group_by_func(txs):
    grouped = defaultdict(list)
    for tx in txs:
        for func_name in FUNC_HASHES:
            if is_call(tx, func_name):
                grouped[func_name].append(tx)
    return grouped


def compute_locked_amount_evolution(transactions):
    current_locked = 0
    locked_amounts = []
    timestamps = []
    for tx in transactions:
        is_lock = is_call(tx, "lock")
        if is_lock or is_call(tx, "free"):
            sign = 1 if is_lock else -1
            locked_amount = get_locked_amount(tx)
            if locked_amount >= 30_000:
                print(tx["hash"], locked_amount, sign, get_time(tx).isoformat())
            current_locked += locked_amount * sign
            locked_amounts.append(current_locked)
            timestamps.append(get_time(tx))
    return locked_amounts, timestamps


def hash_addresses(addresses):
    return web3.Web3.soliditySha3(
        ["address[]"],
        [[web3.Web3.toChecksumAddress(a) for a in addresses]]
    ).hex()


class State:
    def __init__(self):
        self.slates = defaultdict(list)
        self.votes = defaultdict(int)
        self.deposits = defaultdict(int)
        self.approvals = defaultdict(int)
        self.timestamp = None
        self.hat = None

    def add_weight(self, weight, slate):
        yays = self.slates[slate]
        for yay in yays:
            self.approvals[yay] += weight

    def sub_weight(self, weight, slate):
        yays = self.slates[slate]
        for yay in yays:
            self.approvals[yay] -= weight

    def lock(self, tx):
        sender = tx["from"]
        wad = get_locked_amount(tx)
        self.deposits[sender] += wad
        self.add_weight(wad, sender)

    def free(self, tx):
        sender = tx["from"]
        wad = get_locked_amount(tx)
        self.deposits[sender] -= wad
        self.sub_weight(wad, sender)

    def _etch(self, addresses):
        addresses_hash = hash_addresses(addresses)
        self.slates[addresses_hash] = addresses
        return addresses_hash

    def etch(self, tx):
        addresses = get_vote_addresses(tx)
        return self._etch(addresses)

    def vote(self, tx):
        addresses = get_vote_addresses(tx)
        slate = self._etch(addresses)
        self._vote_slate(tx, slate)
        return slate

    def vote_slate(self, tx):
        slate = get_slate(tx)
        self._vote_slate(tx, slate)

    def _vote_slate(self, tx, slate):
        if slate not in self.slates:
            logging.warning("slate not found %s", slate)
        sender = tx["from"]
        weight = self.deposits[sender]
        self.sub_weight(weight, self.votes[sender])
        self.votes[sender] = slate
        self.add_weight(weight, slate)

    def lift(self, tx):
        self.hat = get_lift_address(tx)

    def process_tx(self, tx):
        self.timestamp = get_time(tx)
        for func_name in FUNC_HASHES:
            if is_call(tx, func_name):
                getattr(self, func_name)(tx)


def compute_votes_evolution(transactions):
    state = State()
    states = []
    for tx in transactions:
        state = copy.deepcopy(state)
        state.process_tx(tx)
        states.append(state)
    return states



def plot_locked(transactions, output=None):
    locked_amounts, timestamps = compute_locked_amount_evolution(transactions)

    fig, ax = plt.subplots()
    ax.plot(timestamps, locked_amounts)
    ax.set_xlabel("Date")
    ax.set_ylabel("MKR amount")
    fig.autofmt_xdate(rotation=45)
    plt.tight_layout()

    if output is None:
        plt.show()
    else:
        plt.savefig(output)


parser = argparse.ArgumentParser(prog="analyze-mkr-txs")
parser.add_argument("-d", "--data-dir", default=DATA_DIR, help="data directory")
subparsers = parser.add_subparsers(dest="command")

plot_locked_parser = subparsers.add_parser("plot-locked")
plot_locked_parser.add_argument("-o", "--output", help="output file")


def main():
    args = parser.parse_args()

    transactions = sorted(load_data(args.data_dir)["result"], key=lambda x: x["timeStamp"])
    successful_transactions = [tx for tx in transactions if tx["isError"] == "0"]

    if not args.command:
        parser.error("no command given")

    if args.command == "plot-locked":
        plot_locked(successful_transactions, args.output)


if __name__ == "__main__":
    main()
