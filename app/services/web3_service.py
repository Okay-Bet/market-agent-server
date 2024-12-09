from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from ..config import (
    POLYGON_RPC, PRIVATE_KEY, USDC_ADDRESS, CTF_ADDRESS,
    EXCHANGE_ADDRESS, USDC_ABI, CTF_ABI, logger
)

class Web3Service:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.wallet_address = self.w3.eth.account.from_key(PRIVATE_KEY).address
        
        # Initialize contracts
        self.usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=USDC_ABI
        )
        self.ctf = self.w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_ABI
        )

    def approve_usdc(self):
        try:
            logger.info("Starting USDC approval process...")
            max_amount = int("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 30_000_000_000  # 30 gwei
            max_fee = base_fee * 3 + priority_fee

            txn = self.usdc.functions.approve(
                Web3.to_checksum_address(EXCHANGE_ADDRESS),
                max_amount
            ).build_transaction({
                'chainId': 137,
                'gas': 100000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address
            })

            signed_txn = self.w3.eth.account.sign_transaction(txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

            if receipt['status'] != 1:
                raise ValueError("Approval transaction failed")

            return {
                "success": True,
                "tx_hash": receipt['transactionHash'].hex()
            }
        except Exception as e:
            logger.error(f"USDC approval failed: {str(e)}")
            raise ValueError(f"Failed to approve USDC: {str(e)}")

    async def verify_usdc_transaction(self, tx_hash: str, expected_amount: float,
                                    from_address: str, to_address: str) -> dict:
        try:
            logger.info(f"Verifying USDC transfer: {tx_hash}")
            tx_receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            
            if not tx_receipt or not tx_receipt['status']:
                logger.error(f"Transaction failed or not found: {tx_hash}")
                return {
                    'success': False,
                    'error': 'Transaction failed or not found'
                }

            # Verify the transaction details
            tx = self.w3.eth.get_transaction(tx_hash)
            if (tx['from'].lower() != from_address.lower() or
                tx['to'].lower() != self.usdc.address.lower()):
                logger.error(f"Invalid transaction addresses. From: {tx['from']}, To: {tx['to']}")
                return {
                    'success': False,
                    'error': 'Invalid transaction addresses'
                }

            # Decode transfer event
            transfer_events = self.usdc.events.Transfer().process_receipt(tx_receipt)
            if not transfer_events:
                logger.error("No Transfer event found in transaction")
                return {
                    'success': False,
                    'error': 'No Transfer event found'
                }

            transfer_event = transfer_events[0]
            amount = float(transfer_event['args']['value']) / 1e6  # Convert from USDC decimals
            
            logger.info(f"Transfer amount: {amount}, Expected: {expected_amount}")
            
            if abs(amount - expected_amount) > 0.01:  # Allow 1% deviation
                logger.error(f"Invalid transfer amount. Expected: {expected_amount}, Got: {amount}")
                return {
                    'success': False,
                    'error': f'Invalid transfer amount. Expected: {expected_amount}, Got: {amount}'
                }

            logger.info("USDC transfer verification successful")
            return {'success': True}
            
        except Exception as e:
            logger.error(f"USDC verification failed: {str(e)}")
            return {'success': False, 'error': str(e)}