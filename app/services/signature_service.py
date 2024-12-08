from eth_account import Account
from eth_account.messages import encode_typed_data
from hexbytes import HexBytes
import logging

logger = logging.getLogger(__name__)

class SignatureService:
    def __init__(self, w3):
        self.w3 = w3

    def verify_signature(self, order_data: dict, signature: str) -> bool:
        """Verify an EIP-712 signature using eth_account's encode_typed_data."""
        try:
            logger.info("=== Starting EIP-712 Signature Verification ===")

            # Construct the full message object for EIP-712
            # Ensure that your typed data matches the EIP-712 spec exactly.
            # Include EIP712Domain under "types" if you're providing a full_message dictionary.
            typed_data = {
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                    ],
                    "ClobOrder": [
                        {"name": "user_address", "type": "address"},
                        {"name": "market_id", "type": "string"},
                        {"name": "price", "type": "uint256"},
                        {"name": "amount", "type": "uint256"},
                        {"name": "side", "type": "string"},
                        {"name": "nonce", "type": "uint256"},
                    ]
                },
                "primaryType": "ClobOrder",
                "domain": {
                    "name": "ClobOrderDomain",
                    "version": "1",
                    "chainId": 137
                },
                "message": {
                    "user_address": order_data["user_address"],
                    "market_id": order_data["market_id"],
                    "price": int(order_data["price"]),
                    "amount": int(order_data["amount"]),
                    "side": order_data["side"],
                    "nonce": int(order_data["nonce"])
                }
            }


            # Convert signature to bytes if it's a hex string
            if isinstance(signature, str):
                signature = HexBytes(signature)

            # Encode the typed data into a SignableMessage
            signable_message = encode_typed_data(full_message=typed_data)

            # Recover the address that signed the message
            recovered_address = Account.recover_message(signable_message, signature=signature)

            logger.info(f"Recovered address: {recovered_address}")
            logger.info(f"Expected address: {order_data['user_address']}")

            return recovered_address.lower() == order_data['user_address'].lower()

        except Exception as e:
            logger.error(f"Signature verification failed: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Error message: {str(e)}")
            return False
