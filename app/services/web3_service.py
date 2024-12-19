# src/services/web3_service.py
from web3 import Web3
import asyncio
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

        self.required_addresses = {
            'exchange': '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E',
            'neg_risk_exchange': '0xC5d563A36AE78145C45a50134d48A1215220f80a',
            'neg_risk_adapter': '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296'
        }

        self.ctf = self.w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_ABI
        )

    async def transfer_usdc(self, to_address: str, amount: int) -> dict:
        """
        Transfer USDC to a specified address
        
        Args:
            to_address: Recipient address
            amount: Amount in USDC base units (6 decimals)
        """
        try:
            logger.info(f"Initiating USDC transfer to {to_address} of {amount} units")
            
            # Get current gas prices
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 50_000_000_000  # 50 gwei
            max_fee = base_fee * 4 + priority_fee

            # Build transaction
            txn = self.usdc.functions.transfer(
                self.w3.to_checksum_address(to_address),
                amount
            ).build_transaction({
                'chainId': 137,
                'gas': 100000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address
            })

            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            
            # Wait for transaction receipt with increased timeout
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

            if receipt['status'] != 1:
                raise ValueError("Transfer transaction failed")

            return {
                "success": True,
                "transaction_hash": receipt['transactionHash'].hex(),
                "amount_transferred": amount,
                "recipient": to_address
            }
            
        except Exception as e:
            logger.error(f"USDC transfer failed: {str(e)}")
            raise ValueError(f"Failed to transfer USDC: {str(e)}")

    def approve_usdc(self):
        try:
            logger.info("Starting USDC approval process...")
            max_amount = int("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)
            
            # Increase base fee multiplier and priority fee
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 50_000_000_000  # 50 gwei
            max_fee = base_fee * 4 + priority_fee  # Increased from 3x to 4x

            txn = self.usdc.functions.approve(
                self.w3.to_checksum_address(EXCHANGE_ADDRESS),
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
            
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

            if receipt['status'] != 1:
                raise ValueError("Approval transaction failed")

            return {
                "success": True,
                "tx_hash": receipt['transactionHash'].hex()
            }
        except Exception as e:
            logger.error(f"USDC approval failed: {str(e)}")
            raise ValueError(f"Failed to approve USDC: {str(e)}")

    async def approve_all_contracts(self):
        """Approve all required contracts for both USDC and CTF"""
        try:
            logger.info("Starting approval process for all contracts...")
            
            for name, address in self.required_addresses.items():
                logger.info(f"Approving {name} at {address}")
                
                # Approve USDC
                try:
                    logger.info(f"Approving USDC for {name}")
                    max_amount = int("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)
                    
                    current_allowance = self.usdc.functions.allowance(
                        self.wallet_address,
                        self.w3.to_checksum_address(address)
                    ).call()
                    
                    if current_allowance < max_amount:
                        base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
                        priority_fee = 50_000_000_000  # 50 gwei
                        max_fee = base_fee * 4 + priority_fee

                        txn = self.usdc.functions.approve(
                            self.w3.to_checksum_address(address),
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
                        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
                        
                        if receipt['status'] != 1:
                            raise ValueError(f"USDC approval failed for {name}")
                        
                        logger.info(f"USDC approval successful for {name}")
                        await asyncio.sleep(2)  # Wait for approval to propagate
                
                except Exception as e:
                    logger.error(f"USDC approval failed for {name}: {str(e)}")
                    raise

            return {"success": True}
                    
        except Exception as e:
            logger.error(f"Contract approval process failed: {str(e)}")
            return {"success": False, "error": str(e)}

    def check_all_approvals(self) -> dict:
        """Check approvals for all required addresses"""
        try:
            results = {}
            for name, address in self.required_addresses.items():
                # Check USDC allowance
                usdc_allowance = self.usdc.functions.allowance(
                    self.wallet_address,
                    self.w3.to_checksum_address(address)
                ).call()
                
                # Check CTF approval
                ctf_approved = self.ctf.functions.isApprovedForAll(
                    self.wallet_address,
                    self.w3.to_checksum_address(address)
                ).call()
                
                results[name] = {
                    "usdc_allowance": usdc_allowance,
                    "ctf_approved": ctf_approved
                }
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to check approvals: {str(e)}")
            raise