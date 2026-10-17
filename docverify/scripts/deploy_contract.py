#!/usr/bin/env python
"""
Compile contracts/DocumentRegistry.sol with py-solc-x and deploy it to a
running Ganache instance using its first pre-funded dev account.

Usage:
    python scripts/deploy_contract.py

Requires Ganache to already be running (default http://127.0.0.1:7545).
On first run this will also download solc 0.8.19 into ~/.solcx if it isn't
installed yet, which needs an internet connection once.

After it prints the deployed address, copy it into .env as CONTRACT_ADDRESS
(and DEPLOYER_PRIVATE_KEY as the private key of the account that deployed
it, so the backend can sign registerDocument/revokeDocument transactions
as an authorized issuer - the constructor automatically authorizes the
deploying account).
"""
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import solcx
from web3 import Web3

SOLC_VERSION = "0.8.19"
CONTRACT_PATH = BASE_DIR / "contracts" / "DocumentRegistry.sol"
BUILD_DIR = BASE_DIR / "contracts" / "build"
ABI_PATH = BUILD_DIR / "DocumentRegistry.abi.json"
BIN_PATH = BUILD_DIR / "DocumentRegistry.bin"

# Falls back to Ganache's default RPC URL / chain id if .env hasn't been
# filled in yet - deploying is normally the very first thing you do, before
# CONTRACT_ADDRESS exists, so we can't rely on Django settings here.
WEB3_PROVIDER_URI = os.environ.get("WEB3_PROVIDER_URI", "http://127.0.0.1:7545")


def ensure_solc_installed():
    installed = [str(v) for v in solcx.get_installed_solc_versions()]
    if SOLC_VERSION not in installed:
        print(f"Installing solc {SOLC_VERSION} (one-time download)...")
        solcx.install_solc(SOLC_VERSION)
    solcx.set_solc_version(SOLC_VERSION)


def compile_contract():
    if not CONTRACT_PATH.exists():
        sys.exit(f"Contract source not found at {CONTRACT_PATH}")

    print(f"Compiling {CONTRACT_PATH.relative_to(BASE_DIR)} with solc {SOLC_VERSION}...")
    compiled = solcx.compile_files(
        [str(CONTRACT_PATH)],
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
    )
    # compile_files keys results as "<path>:<ContractName>"
    key = next(k for k in compiled if k.endswith(":DocumentRegistry"))
    contract_interface = compiled[key]

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with open(ABI_PATH, "w") as f:
        json.dump(contract_interface["abi"], f, indent=2)
    with open(BIN_PATH, "w") as f:
        f.write(contract_interface["bin"])

    print(f"  ABI     -> {ABI_PATH.relative_to(BASE_DIR)}")
    print(f"  Bytecode -> {BIN_PATH.relative_to(BASE_DIR)}")
    return contract_interface["abi"], contract_interface["bin"]


def deploy(abi, bytecode):
    w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER_URI, request_kwargs={"timeout": 10}))
    if not w3.is_connected():
        sys.exit(
            f"Could not reach a node at {WEB3_PROVIDER_URI}.\n"
            f"Start Ganache first (GUI app, or `ganache --port 7545`), then re-run this script."
        )

    accounts = w3.eth.accounts
    if not accounts:
        sys.exit("Node returned no accounts - is Ganache configured with dev accounts unlocked?")

    deployer = accounts[0]
    balance = w3.from_wei(w3.eth.get_balance(deployer), "ether")
    print(f"Deploying from {deployer} (balance: {balance} ETH)...")

    DocumentRegistry = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = DocumentRegistry.constructor().transact({"from": deployer})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    print(f"Deployed! Contract address: {receipt.contractAddress}")
    print(f"  tx hash: {receipt.transactionHash.hex()}")
    print(f"  block:   {receipt.blockNumber}")
    print(f"  gas used: {receipt.gasUsed}")

    return receipt.contractAddress, deployer


def main():
    ensure_solc_installed()
    abi, bytecode = compile_contract()
    address, deployer = deploy(abi, bytecode)

    print()
    print("=" * 70)
    print("Next steps - add these to your .env file:")
    print("=" * 70)
    print(f"CONTRACT_ADDRESS={address}")
    print(
        "DEPLOYER_PRIVATE_KEY=<private key of the deploying account, "
        "copy it from Ganache's Accounts tab / CLI output>"
    )
    print(f"WEB3_PROVIDER_URI={WEB3_PROVIDER_URI}")
    print("=" * 70)
    print(
        f"Note: account {deployer} was automatically authorized as an issuer "
        f"in the contract constructor. Any other account can be authorized "
        f"later via authorizeIssuer()."
    )


if __name__ == "__main__":
    main()
