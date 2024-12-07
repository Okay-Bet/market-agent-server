from eth_account.messages import encode_defunct
from web3 import Web3
from typing import Dict, Any

class SignatureService:
    def __init__(self, w3: Web3):
        self.w3 = w3

    def verify_signature(self, order_data: Dict[str, Any], signature: str) -> bool:
        """Verify that the order was signed by the user"""
        # Create message hash
        message = self._create_message_hash(order_data)
        message_hash = encode_defunct(text=message)
        
        try:
            # Recover signer address
            signer = self.w3.eth.account.recover_message(message_hash, signature=signature)
            return signer.lower() == order_data['user_address'].lower()
        except Exception as e:
            logger.error(f"Signature verification failed: {str(e)}")
            return False

    def _create_message_hash(self, order_data: Dict[str, Any]) -> str:
        """Create a deterministic message hash from order data"""
        return f"""
        Market: {order_data['market_id']}
        Price: {order_data['price']}
        Amount: {order_data['amount']}
        Side: {order_data['side']}
        Nonce: {order_data['nonce']}
        User: {order_data['user_address']}
        """
