# src/services/web3_service.py
import asyncio
from web3 import Web3
from web3.contract import Contract
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

        self.ctf: Contract = self.w3.eth.contract(
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
        """
        Approve all required contracts for both USDC and CTF tokens.
        Implements approval checks and handles both ERC20 (USDC) and ERC1155 (CTF) approvals.
        
        Returns:
            dict: Status of approval process with success flag and any error details
        """
        try:
            logger.info("Starting approval process for all contracts...")
            MAX_UINT256 = int("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)
            
            for name, address in self.required_addresses.items():
                try:
                    checksum_address = self.w3.to_checksum_address(address)
                    logger.info(f"Processing approvals for {name} at {checksum_address}")

                    # Step 1: Check current approval states
                    current_approvals = {
                        "usdc": self.usdc.functions.allowance(
                            self.wallet_address,
                            checksum_address
                        ).call(),
                        "ctf": self.ctf.functions.isApprovedForAll(
                            self.wallet_address,
                            checksum_address
                        ).call()
                    }

                    logger.info(f"Current approvals for {name}: USDC={current_approvals['usdc']}, CTF={current_approvals['ctf']}")

                    # Step 2: Handle CTF (ERC1155) approval first
                    if not current_approvals['ctf']:
                        logger.info(f"Setting CTF approval for {name}")
                        
                        # Get current gas prices
                        base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
                        priority_fee = 50_000_000_000  # 50 gwei
                        max_fee = base_fee * 4 + priority_fee  # 4x multiplier for quick inclusion

                        ctf_txn = self.ctf.functions.setApprovalForAll(
                            checksum_address,
                            True
                        ).build_transaction({
                            'chainId': 137,  # Polygon mainnet
                            'gas': 150000,   # Higher gas limit for CTF approval
                            'maxFeePerGas': max_fee,
                            'maxPriorityFeePerGas': priority_fee,
                            'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                            'from': self.wallet_address
                        })

                        signed_txn = self.w3.eth.account.sign_transaction(ctf_txn, PRIVATE_KEY)
                        tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                        
                        logger.info(f"Waiting for CTF approval transaction: {tx_hash.hex()}")
                        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                        
                        if receipt['status'] != 1:
                            raise ValueError(f"CTF approval transaction failed for {name}")
                        
                        logger.info(f"CTF approval successful for {name}")
                        await asyncio.sleep(2)  # Wait for approval to propagate
                    else:
                        logger.info(f"CTF already approved for {name}")

                    # Step 3: Handle USDC (ERC20) approval if needed
                    if current_approvals['usdc'] < MAX_UINT256:
                        logger.info(f"Setting USDC approval for {name}")
                        
                        # Reuse the same gas price calculation
                        base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
                        priority_fee = 50_000_000_000  # 50 gwei
                        max_fee = base_fee * 4 + priority_fee

                        usdc_txn = self.usdc.functions.approve(
                            checksum_address,
                            MAX_UINT256
                        ).build_transaction({
                            'chainId': 137,
                            'gas': 100000,  # Standard gas limit for ERC20 approval
                            'maxFeePerGas': max_fee,
                            'maxPriorityFeePerGas': priority_fee,
                            'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                            'from': self.wallet_address
                        })

                        signed_txn = self.w3.eth.account.sign_transaction(usdc_txn, PRIVATE_KEY)
                        tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                        
                        logger.info(f"Waiting for USDC approval transaction: {tx_hash.hex()}")
                        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                        
                        if receipt['status'] != 1:
                            raise ValueError(f"USDC approval transaction failed for {name}")
                        
                        logger.info(f"USDC approval successful for {name}")
                        await asyncio.sleep(2)  # Wait for approval to propagate
                    else:
                        logger.info(f"USDC already at max allowance for {name}")

                    # Verify final approval states
                    final_approvals = {
                        "usdc": self.usdc.functions.allowance(
                            self.wallet_address,
                            checksum_address
                        ).call(),
                        "ctf": self.ctf.functions.isApprovedForAll(
                            self.wallet_address,
                            checksum_address
                        ).call()
                    }
                    
                    if not final_approvals['ctf'] or final_approvals['usdc'] < MAX_UINT256:
                        raise ValueError(f"Final approval verification failed for {name}")
                    
                    logger.info(f"All approvals successfully verified for {name}")

                except Exception as e:
                    logger.error(f"Approval process failed for {name}: {str(e)}")
                    raise ValueError(f"Failed to process approvals for {name}: {str(e)}")

            return {"success": True}
                        
        except Exception as e:
            error_msg = f"Contract approval process failed: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

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