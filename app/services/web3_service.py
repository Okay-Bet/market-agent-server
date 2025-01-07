# src/services/web3_service.py
import asyncio
from web3 import Web3
from web3.contract import Contract
from web3.middleware import ExtraDataToPOAMiddleware
from ..config import (
    POLYGON_RPC, PRIVATE_KEY, USDC_ADDRESS, CTF_ADDRESS,
    EXCHANGE_ADDRESS, USDC_ABI, CTF_ABI, logger,
    ACROSS_SPOKE_POOL_ADDRESS, ACROSS_SPOKE_POOL_ABI 
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

        self.spoke_pool = self.w3.eth.contract(
            address=Web3.to_checksum_address(ACROSS_SPOKE_POOL_ADDRESS),
            abi=ACROSS_SPOKE_POOL_ABI
        )

        self.required_addresses = {
            'exchange': '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E',
            'neg_risk_exchange': '0xC5d563A36AE78145C45a50134d48A1215220f80a',
            'neg_risk_adapter': '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296',
            'across_spoke_pool': ACROSS_SPOKE_POOL_ADDRESS
        }

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

    async def approve_token(self, token_contract: Contract, spender_address: str, amount: int) -> dict:
        """
        Approve token spending with robust gas handling for Polygon network
        
        Args:
            token_contract: The token contract instance
            spender_address: Address to approve for spending
            amount: Amount to approve (in base units)
                
        Returns:
            dict: Transaction receipt details
        """
        try:
            spender = Web3.to_checksum_address(spender_address)
            logger.info(f"Starting approval process for {amount} tokens for spender {spender}")
            
            # Get current allowance
            current_allowance = token_contract.functions.allowance(
                self.wallet_address,
                spender
            ).call()
            
            logger.info(f"Current allowance: {current_allowance} base units")
            
            def build_tx(func, gas_multiplier=1.5):
                """Helper to build transaction with appropriate gas settings"""
                # Get latest gas parameters
                latest_block = self.w3.eth.get_block('latest')
                base_fee = latest_block['baseFeePerGas']
                
                # Use much higher priority fee for Polygon
                priority_fee = 100_000_000_000  # 100 gwei
                max_fee = int(base_fee * 2.5 + priority_fee)  # More aggressive max fee
                
                # Estimate gas with a safety margin
                gas_estimate = func.estimate_gas({
                    'from': self.wallet_address,
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': priority_fee
                })
                gas_limit = int(gas_estimate * gas_multiplier)
                
                return func.build_transaction({
                    'chainId': 137,
                    'gas': gas_limit,
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': priority_fee,
                    'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                    'from': self.wallet_address
                })

            # First reset allowance if needed
            if current_allowance > 0:
                logger.info("Resetting allowance to 0")
                reset_func = token_contract.functions.approve(spender, 0)
                reset_txn = build_tx(reset_func)
                
                signed_reset = self.w3.eth.account.sign_transaction(reset_txn, PRIVATE_KEY)
                reset_hash = self.w3.eth.send_raw_transaction(signed_reset.raw_transaction)
                
                try:
                    # Increased timeout and added polling
                    for _ in range(30):  # 5 minutes total
                        try:
                            reset_receipt = self.w3.eth.get_transaction_receipt(reset_hash)
                            if reset_receipt:
                                if reset_receipt['status'] != 1:
                                    raise ValueError("Reset allowance transaction failed")
                                logger.info("Successfully reset allowance to 0")
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(10)  # Wait 10 seconds between checks
                    else:
                        raise TimeoutError("Reset allowance transaction timed out")
                except Exception as e:
                    logger.error(f"Reset allowance failed: {str(e)}")
                    raise

            # Now set new approval
            logger.info("Setting new approval to maximum value")
            max_uint256 = 2**256 - 1
            
            approve_func = token_contract.functions.approve(spender, max_uint256)
            approve_txn = build_tx(approve_func)
            
            signed_txn = self.w3.eth.account.sign_transaction(approve_txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            
            logger.info(f"Approval transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt with increased timeout and polling
            for _ in range(30):  # 5 minutes total
                try:
                    receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                    if receipt:
                        if receipt['status'] != 1:
                            raise ValueError("Approval transaction failed")
                        logger.info(f"Approval transaction confirmed in block {receipt['blockNumber']}")
                        break
                except Exception:
                    pass
                await asyncio.sleep(10)  # Wait 10 seconds between checks
            else:
                raise TimeoutError("Approval transaction timed out")
            
            # Verify final allowance
            final_allowance = token_contract.functions.allowance(
                self.wallet_address,
                spender
            ).call()
            
            logger.info(f"Final allowance verified: {final_allowance} base units")
            
            if final_allowance < amount:
                raise ValueError(f"Final allowance ({final_allowance}) less than required ({amount})")

            return {
                "success": True,
                "transaction_hash": receipt['transactionHash'].hex(),
                "gas_used": receipt['gasUsed'],
                "final_allowance": final_allowance
            }

        except Exception as e:
            logger.error(f"Token approval failed: {str(e)}")
            raise ValueError(f"Failed to approve token: {str(e)}")

    async def send_across_deposit(self, deposit_params: dict, token_contract: Contract) -> dict:
        """
        Execute a bridge deposit through Across Protocol with enhanced error handling
        
        Args:
            deposit_params: Dictionary containing bridge parameters
            token_contract: The specific USDC contract to use for the bridge
        
        Returns:
            dict: Transaction details including hash and status
        """
        try:
            logger.info(f"Initiating Across bridge deposit with params: {deposit_params}")
            
            # First, check token balance
            balance = token_contract.functions.balanceOf(self.wallet_address).call()
            logger.info(f"Current token balance: {balance} base units")
            
            if balance < deposit_params['inputAmount']:
                raise ValueError(f"Insufficient balance. Have: {balance}, Need: {deposit_params['inputAmount']}")

            spoke_pool = self.w3.eth.contract(
                address=Web3.to_checksum_address(deposit_params['spoke_pool_address']),
                abi=ACROSS_SPOKE_POOL_ABI
            )

            # Handle approval first
            try:
                await self.approve_token(
                    token_contract=token_contract,
                    spender_address=deposit_params['spoke_pool_address'],
                    amount=deposit_params['inputAmount']
                )
            except Exception as e:
                raise ValueError(f"Failed to approve token: {str(e)}")

            # Prepare the function call data
            deposit_func = spoke_pool.functions.depositV3(
                Web3.to_checksum_address(deposit_params['depositor']),
                Web3.to_checksum_address(deposit_params['recipient']),
                token_contract.address,
                Web3.to_checksum_address(deposit_params['outputToken']),
                int(deposit_params['inputAmount']),
                int(deposit_params['outputAmount']),
                int(deposit_params['destinationChainId']),
                Web3.to_checksum_address(deposit_params['exclusiveRelayer']),
                int(deposit_params['quoteTimestamp']) & 0xFFFFFFFF,
                int(deposit_params['fillDeadline']) & 0xFFFFFFFF,
                int(deposit_params['exclusivityDeadline']) & 0xFFFFFFFF,
                deposit_params['message']
            )

            # First simulate the transaction
            try:
                logger.info("Simulating transaction before sending...")
                sim_result = deposit_func.call(
                    {
                        'from': self.wallet_address,
                        'value': 0
                    }
                )
                logger.info(f"Simulation successful: {sim_result}")
            except Exception as e:
                # Try to decode the revert reason
                revert_msg = str(e)
                if 'revert' in revert_msg.lower():
                    try:
                        # Extract hex data from error message if present
                        hex_data = revert_msg[revert_msg.find('0x'):]
                        decoded = spoke_pool.decode_function_result('depositV3', bytes.fromhex(hex_data[2:]))
                        logger.error(f"Decoded revert reason: {decoded}")
                    except Exception as decode_error:
                        logger.error(f"Failed to decode revert reason: {decode_error}")
                raise ValueError(f"Transaction simulation failed: {revert_msg}")

            # Get current gas prices
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 50_000_000_000  # 50 gwei
            max_fee = base_fee * 4 + priority_fee

            # Build transaction
            txn = deposit_func.build_transaction({
                'chainId': 137,
                'gas': 500000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address,
                'value': 0
            })

            # Log detailed transaction data for debugging
            logger.info(f"""
            Transaction Details:
            From: {txn['from']}
            To: {txn['to']}
            Value: {txn['value']}
            Gas: {txn['gas']}
            MaxFeePerGas: {txn['maxFeePerGas']}
            MaxPriorityFeePerGas: {txn['maxPriorityFeePerGas']}
            Nonce: {txn['nonce']}
            """)

            signed_txn = self.w3.eth.account.sign_transaction(txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            
            logger.info(f"Bridge deposit transaction sent: {tx_hash.hex()}")
            
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt['status'] != 1:
                # Get more details about the failed transaction
                try:
                    failed_tx = self.w3.eth.call(
                        {
                            'from': txn['from'],
                            'to': txn['to'],
                            'data': txn['data'],
                            'value': txn['value'],
                            'gas': txn['gas'],
                            'maxFeePerGas': txn['maxFeePerGas'],
                            'maxPriorityFeePerGas': txn['maxPriorityFeePerGas'],
                        },
                        receipt['blockNumber'] - 1
                    )
                    raise ValueError(f"Transaction failed with details: {failed_tx}")
                except Exception as call_error:
                    raise ValueError(f"Bridge deposit transaction failed: {str(call_error)}")
            
            return {
                "success": True,
                "transaction_hash": receipt['transactionHash'].hex(),
                "block_number": receipt['blockNumber'],
                "gas_used": receipt['gasUsed'],
                "bridge_details": {
                    "input_amount": deposit_params['inputAmount'],
                    "output_amount": deposit_params['outputAmount'],
                    "estimated_time": 900
                }
            }
            
        except Exception as e:
            logger.error(f"Bridge deposit failed: {str(e)}")
            raise ValueError(f"Failed to execute bridge deposit: {str(e)}")