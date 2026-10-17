"""
Web3.py wrapper around the deployed DocumentRegistry contract.

Only the document's SHA-256 hash ever touches the chain. Every
chain-writing operation (register/revoke) is signed locally with
DEPLOYER_PRIVATE_KEY and broadcast as a raw transaction - the backend
acts as a single custodial "notary" wallet on behalf of whichever Django
user is logged in as an issuer, which keeps this demo/marketplace build
free of a wallet-connect flow while still giving every registration a
real, independently-verifiable on-chain transaction.

All public functions raise a subclass of BlockchainError with a message
written for a developer reading server logs (e.g. "start Ganache and
re-run scripts/deploy_contract.py"), never a raw web3/requests traceback.
"""
import json
import logging

from django.conf import settings
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class BlockchainError(Exception):
    """Base class for all friendly blockchain errors."""


class NodeUnreachableError(BlockchainError):
    def __init__(self, uri):
        super().__init__(
            f"Could not reach the blockchain node at {uri}. "
            f"Is Ganache running? Start it and make sure WEB3_PROVIDER_URI in "
            f".env points at it (default: http://127.0.0.1:7545)."
        )


class ContractNotDeployedError(BlockchainError):
    def __init__(self, reason):
        super().__init__(
            f"DocumentRegistry contract is not available ({reason}). "
            f"Run `python scripts/deploy_contract.py` with Ganache running, "
            f"then copy the printed address into CONTRACT_ADDRESS in your .env file."
        )


class InsufficientFundsError(BlockchainError):
    def __init__(self, address):
        super().__init__(
            f"Wallet {address} does not have enough ETH to pay gas for this "
            f"transaction. If you're using Ganache, pick one of the pre-funded "
            f"dev accounts and put its private key in DEPLOYER_PRIVATE_KEY."
        )


class DocumentAlreadyRegisteredError(BlockchainError):
    def __init__(self, doc_hash_hex):
        super().__init__(f"Document hash {doc_hash_hex} is already registered on-chain.")


class DocumentNotFoundError(BlockchainError):
    def __init__(self, doc_hash_hex):
        super().__init__(f"No on-chain record found for hash {doc_hash_hex}.")


class NotAuthorizedIssuerError(BlockchainError):
    def __init__(self, address):
        super().__init__(
            f"Wallet {address} is not an authorized issuer on the contract. "
            f"Ask the contract owner to call authorizeIssuer() for this address."
        )


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
def get_web3() -> Web3:
    """Connect to the configured node (Ganache by default). Raises NodeUnreachableError if unreachable."""
    w3 = Web3(Web3.HTTPProvider(settings.WEB3_PROVIDER_URI, request_kwargs={'timeout': 10}))
    try:
        connected = w3.is_connected()
    except Exception:
        connected = False
    if not connected:
        raise NodeUnreachableError(settings.WEB3_PROVIDER_URI)
    return w3


def get_contract(w3: Web3 = None):
    """Load the DocumentRegistry ABI + address and return a web3 contract instance."""
    if w3 is None:
        w3 = get_web3()

    if not settings.CONTRACT_ADDRESS:
        raise ContractNotDeployedError("CONTRACT_ADDRESS is not set in .env")

    if not settings.CONTRACT_ABI_PATH.exists():
        raise ContractNotDeployedError(f"ABI file not found at {settings.CONTRACT_ABI_PATH}")

    try:
        with open(settings.CONTRACT_ABI_PATH) as f:
            abi = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractNotDeployedError(f"could not read/parse ABI file ({exc})")

    try:
        address = Web3.to_checksum_address(settings.CONTRACT_ADDRESS)
    except ValueError:
        raise ContractNotDeployedError(f"CONTRACT_ADDRESS '{settings.CONTRACT_ADDRESS}' is not a valid address")

    # A configured address with no bytecode behind it almost always means the
    # chain was reset since the contract was deployed (e.g. the Ganache GUI
    # was restarted, which wipes all state unless you're using a saved
    # workspace) - every call would otherwise fail deep inside web3 with an
    # opaque BadFunctionCallOutput instead of a message that says what to do.
    if w3.eth.get_code(address) in (b'', '0x'):
        raise ContractNotDeployedError(
            f"no contract code found at {address} on {settings.WEB3_PROVIDER_URI}. "
            f"If you restarted Ganache since deploying, its chain state (and the "
            f"contract on it) was wiped"
        )

    return w3.eth.contract(address=address, abi=abi)


def _get_signing_account(w3: Web3):
    if not settings.DEPLOYER_PRIVATE_KEY:
        raise BlockchainError(
            "DEPLOYER_PRIVATE_KEY is not set in .env. Copy a private key from one "
            "of Ganache's dev accounts into it."
        )
    return w3.eth.account.from_key(settings.DEPLOYER_PRIVATE_KEY)


def _hash_to_bytes32(doc_hash_hex: str) -> bytes:
    """SHA-256 hex digests are exactly 32 bytes - the same width as Solidity's bytes32."""
    doc_hash_hex = doc_hash_hex.lower()
    if doc_hash_hex.startswith('0x'):
        doc_hash_hex = doc_hash_hex[2:]
    if len(doc_hash_hex) != 64:
        raise BlockchainError(f"'{doc_hash_hex}' is not a valid 32-byte (SHA-256) hash.")
    try:
        return bytes.fromhex(doc_hash_hex)
    except ValueError:
        raise BlockchainError(f"'{doc_hash_hex}' is not valid hex.")


def _send_signed_tx(w3: Web3, account, fn):
    """Build, sign, send and wait for receipt for a state-changing contract call."""
    try:
        tx = fn.build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address, 'pending'),
            'chainId': settings.CHAIN_ID,
        })
    except ContractLogicError as exc:
        raise _translate_contract_error(exc, account.address)

    signed = w3.eth.account.sign_transaction(tx, private_key=settings.DEPLOYER_PRIVATE_KEY)

    try:
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    except ValueError as exc:
        raise _translate_send_error(exc, account.address)

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    except TimeExhausted:
        raise BlockchainError(
            f"Transaction {tx_hash.hex()} was sent but not mined within the timeout. "
            f"Check Ganache is still running."
        )

    return receipt


def _translate_contract_error(exc: ContractLogicError, address: str) -> BlockchainError:
    msg = str(exc)
    if "already registered" in msg:
        return DocumentAlreadyRegisteredError("(see revert reason)")
    if "not authorized" in msg or "not an authorized issuer" in msg:
        return NotAuthorizedIssuerError(address)
    if "not found" in msg:
        return DocumentNotFoundError("(see revert reason)")
    return BlockchainError(f"Contract rejected the transaction: {msg}")


def _translate_send_error(exc: ValueError, address: str) -> BlockchainError:
    msg = str(exc)
    lowered = msg.lower()
    if "insufficient funds" in lowered:
        return InsufficientFundsError(address)
    if "already known" in lowered or "already registered" in lowered:
        return DocumentAlreadyRegisteredError("(see node error)")
    if "revert" in lowered:
        if "already registered" in lowered:
            return DocumentAlreadyRegisteredError("(see node error)")
        if "not an authorized issuer" in lowered or "not authorized" in lowered:
            return NotAuthorizedIssuerError(address)
        return BlockchainError(f"Contract rejected the transaction: {msg}")
    return BlockchainError(f"Failed to send transaction: {msg}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def register_document_onchain(doc_hash_hex: str, document_type: str, metadata_uri: str = "") -> dict:
    """
    Register a document hash on-chain. Returns {tx_hash, block_number, status}.
    status is True if the transaction succeeded, False if it was mined but reverted.
    """
    w3 = get_web3()
    contract = get_contract(w3)
    account = _get_signing_account(w3)
    doc_hash = _hash_to_bytes32(doc_hash_hex)

    fn = contract.functions.registerDocument(doc_hash, document_type, metadata_uri)
    receipt = _send_signed_tx(w3, account, fn)

    if receipt.status == 0:
        raise DocumentAlreadyRegisteredError(doc_hash_hex)

    logger.info("Registered document %s on-chain, tx=%s block=%s", doc_hash_hex, receipt.transactionHash.hex(), receipt.blockNumber)
    return {
        'tx_hash': receipt.transactionHash.hex(),
        'block_number': receipt.blockNumber,
        'status': receipt.status == 1,
    }


def verify_document_onchain(doc_hash_hex: str) -> dict:
    """
    Look up a document hash on-chain. Returns
    {is_valid, issuer, document_type, metadata_uri, timestamp, revoked}.
    is_valid/issuer will be False/None-ish (zero address) if never registered.
    """
    w3 = get_web3()
    contract = get_contract(w3)
    doc_hash = _hash_to_bytes32(doc_hash_hex)

    is_valid, issuer, document_type, metadata_uri, timestamp, revoked = (
        contract.functions.verifyDocument(doc_hash).call()
    )

    return {
        'is_valid': is_valid,
        'issuer': issuer if int(issuer, 16) != 0 else None,
        'document_type': document_type,
        'metadata_uri': metadata_uri,
        'timestamp': timestamp,
        'revoked': revoked,
        'registered': bool(document_type) or int(issuer, 16) != 0,
    }


def revoke_document_onchain(doc_hash_hex: str) -> dict:
    """Revoke a previously registered document. Returns {tx_hash, block_number, status}."""
    w3 = get_web3()
    contract = get_contract(w3)
    account = _get_signing_account(w3)
    doc_hash = _hash_to_bytes32(doc_hash_hex)

    fn = contract.functions.revokeDocument(doc_hash)
    receipt = _send_signed_tx(w3, account, fn)

    logger.info("Revoked document %s on-chain, tx=%s", doc_hash_hex, receipt.transactionHash.hex())
    return {
        'tx_hash': receipt.transactionHash.hex(),
        'block_number': receipt.blockNumber,
        'status': receipt.status == 1,
    }
