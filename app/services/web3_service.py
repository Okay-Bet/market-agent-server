# src/services/web3_service.py
import asyncio
import time
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

        self.QUICKSWAP_ROUTER = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
        self.ROUTER_ABI = [
            {
                "inputs": [
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
                    {"internalType": "address[]", "name": "path", "type": "address[]"},
                    {"internalType": "address", "name": "to", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"}
                ],
                "name": "swapExactTokensForTokens",
                "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                "stateMutability": "nonpayable",
                "type": "function"
            },
            {
                "inputs": [
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "address[]", "name": "path", "type": "address[]"}
                ],
                "name": "getAmountsOut",
                "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        self.router = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.QUICKSWAP_ROUTER),
            abi=self.ROUTER_ABI
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

    async def approve_token(self, token_contract: Contract, spender_address: str, amount: int, max_retries: int = 3) -> dict:
        """
        Approve token spending with retry mechanism and exponential backoff
        
        Args:
            token_contract: The token contract instance
            spender_address: Address to approve for spending
            amount: Amount to approve (in base units)
            max_retries: Maximum number of retry attempts
                    
        Returns:
            dict: Transaction receipt details
        """
        async def execute_approval(retry_count: int = 0) -> dict:
            try:
                spender = Web3.to_checksum_address(spender_address)
                logger.info(f"Attempt {retry_count + 1}: Starting approval process for {amount} tokens for spender {spender}")
                
                # Get current allowance
                current_allowance = token_contract.functions.allowance(
                    self.wallet_address,
                    spender
                ).call()
                
                logger.info(f"Current allowance: {current_allowance} base units")
                
                def build_tx(func, gas_multiplier=1.5):
                    """Helper to build transaction with appropriate gas settings"""
                    # Increase gas settings with each retry
                    retry_multiplier = 1 + (retry_count * 0.5)  # Increase gas by 50% each retry
                    
                    latest_block = self.w3.eth.get_block('latest')
                    base_fee = latest_block['baseFeePerGas']
                    
                    # Increase priority fee with each retry
                    priority_fee = int(100_000_000_000 * retry_multiplier)  # Start at 100 gwei and increase
                    max_fee = int(base_fee * 5 * retry_multiplier + priority_fee)
                    
                    gas_estimate = func.estimate_gas({
                        'from': self.wallet_address,
                        'maxFeePerGas': max_fee,
                        'maxPriorityFeePerGas': priority_fee
                    })
                    gas_limit = int(gas_estimate * gas_multiplier * retry_multiplier)
                    
                    return func.build_transaction({
                        'chainId': 137,
                        'gas': gas_limit,
                        'maxFeePerGas': max_fee,
                        'maxPriorityFeePerGas': priority_fee,
                        'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                        'from': self.wallet_address
                    })

                # Reset allowance if needed
                if current_allowance > 0:
                    logger.info(f"Attempt {retry_count + 1}: Resetting allowance to 0")
                    reset_func = token_contract.functions.approve(spender, 0)
                    reset_txn = build_tx(reset_func)
                    
                    signed_reset = self.w3.eth.account.sign_transaction(reset_txn, PRIVATE_KEY)
                    reset_hash = self.w3.eth.send_raw_transaction(signed_reset.raw_transaction)
                    
                    # Wait for reset with timeout
                    timeout = 30 * (retry_count + 1)  # Increase timeout with each retry
                    try:
                        async with asyncio.timeout(timeout):
                            while True:
                                try:
                                    reset_receipt = self.w3.eth.get_transaction_receipt(reset_hash)
                                    if reset_receipt:
                                        if reset_receipt['status'] != 1:
                                            raise ValueError("Reset allowance transaction failed")
                                        logger.info(f"Attempt {retry_count + 1}: Successfully reset allowance to 0")
                                        break
                                except Exception:
                                    pass
                                await asyncio.sleep(3)
                    except asyncio.TimeoutError:
                        raise TimeoutError(f"Reset allowance transaction timed out after {timeout} seconds")

                    # Add delay between reset and new approval
                    await asyncio.sleep(3 * (retry_count + 1))

                # Set new approval
                logger.info(f"Attempt {retry_count + 1}: Setting new approval to maximum value")
                max_uint256 = 2**256 - 1
                
                approve_func = token_contract.functions.approve(spender, max_uint256)
                approve_txn = build_tx(approve_func)
                
                signed_txn = self.w3.eth.account.sign_transaction(approve_txn, PRIVATE_KEY)
                tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                
                logger.info(f"Attempt {retry_count + 1}: Approval transaction sent: {tx_hash.hex()}")
                
                # Wait for approval with timeout
                timeout = 30 * (retry_count + 1)
                try:
                    async with asyncio.timeout(timeout):
                        while True:
                            try:
                                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                                if receipt:
                                    if receipt['status'] != 1:
                                        raise ValueError("Approval transaction failed")
                                    logger.info(f"Attempt {retry_count + 1}: Approval transaction confirmed in block {receipt['blockNumber']}")
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(3)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Approval transaction timed out after {timeout} seconds")
                
                # Verify final allowance
                final_allowance = token_contract.functions.allowance(
                    self.wallet_address,
                    spender
                ).call()
                
                logger.info(f"Attempt {retry_count + 1}: Final allowance verified: {final_allowance} base units")
                
                if final_allowance < amount:
                    raise ValueError(f"Final allowance ({final_allowance}) less than required ({amount})")

                return {
                    "success": True,
                    "transaction_hash": receipt['transactionHash'].hex(),
                    "gas_used": receipt['gasUsed'],
                    "final_allowance": final_allowance
                }

            except Exception as e:
                if retry_count < max_retries - 1:
                    # Calculate exponential backoff delay
                    delay = 3 * (2 ** retry_count)  # 3s, 6s, 12s, etc.
                    logger.warning(f"Approval attempt {retry_count + 1} failed: {str(e)}")
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    return await execute_approval(retry_count + 1)
                else:
                    logger.error(f"All approval attempts failed after {max_retries} tries")
                    raise

        try:
            return await execute_approval()
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

    async def swap_usdc_variants(self, amount: int, slippage_percent: float = 0.5) -> dict:
        """
        Swap USDC.e to USDC using Quickswap
        
        Args:
            amount: Amount in base units (6 decimals)
            slippage_percent: Maximum acceptable slippage (default 0.5%)
            
        Returns:
            dict: Transaction details including hash and amounts
        """
        try:
            logger.info(f"Initiating USDC.e to USDC swap for {amount} units")
            
            # Define token addresses
            usdc_e = Web3.to_checksum_address(USDC_ADDRESS)  # Your USDC.e address
            usdc = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")  # Native USDC
            
            # Check USDC.e balance
            usdc_e_balance = self.usdc.functions.balanceOf(self.wallet_address).call()
            if usdc_e_balance < amount:
                raise ValueError(f"Insufficient USDC.e balance. Have: {usdc_e_balance}, Need: {amount}")

            # Get quote from router
            path = [usdc_e, usdc]
            try:
                amounts = self.router.functions.getAmountsOut(amount, path).call()
                expected_output = amounts[1]
                
                # Calculate minimum output with slippage
                min_output = int(expected_output * (1 - slippage_percent / 100))
                
                logger.info(f"""
                Swap Quote:
                Input: {amount} USDC.e
                Expected Output: {expected_output} USDC
                Minimum Output: {min_output} USDC
                Slippage: {slippage_percent}%
                """)
            except Exception as e:
                raise ValueError(f"Failed to get swap quote: {str(e)}")

            # Check and handle approval
            try:
                await self.approve_token(
                    token_contract=self.usdc,
                    spender_address=self.QUICKSWAP_ROUTER,
                    amount=amount
                )
            except Exception as e:
                raise ValueError(f"Failed to approve USDC.e: {str(e)}")

            # Build swap transaction
            deadline = int(time.time()) + 1200  # 20 minutes
            
            # Get current gas prices
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 50_000_000_000  # 50 gwei
            max_fee = base_fee * 4 + priority_fee

            swap_txn = self.router.functions.swapExactTokensForTokens(
                amount,
                min_output,
                path,
                self.wallet_address,
                deadline
            ).build_transaction({
                'chainId': 137,
                'gas': 300000,  # Appropriate gas limit for swaps
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address
            })

            # Sign and send transaction
            signed_txn = self.w3.eth.account.sign_transaction(swap_txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            
            logger.info(f"Swap transaction sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            
            if receipt['status'] != 1:
                raise ValueError("Swap transaction failed")

            # Verify the swap result
            final_usdc_balance = self.bridge_usdc.functions.balanceOf(self.wallet_address).call()
            
            return {
                "success": True,
                "transaction_hash": receipt['transactionHash'].hex(),
                "gas_used": receipt['gasUsed'],
                "input_amount": amount,
                "expected_output": expected_output,
                "minimum_output": min_output,
                "final_usdc_balance": final_usdc_balance
            }
            
        except Exception as e:
            logger.error(f"USDC swap failed: {str(e)}")
            raise ValueError(f"Failed to swap USDC: {str(e)}")
        
    async def get_swap_quote(self, amount: int) -> dict:
        """
        Get quote for USDC.e to USDC swap across different paths
        
        Args:
            amount: Amount in USDC.e base units (6 decimals)
            
        Returns:
            dict: Quote information including different paths and their rates
        """
        try:
            # Define token addresses
            usdc_e = Web3.to_checksum_address(USDC_ADDRESS)  # Your USDC.e address
            usdc = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")  # Native USDC
            usdt = Web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AEb04B58e8F")  # Polygon USDT

            quotes = {}
            
            # Get direct path quote (USDC.e -> USDC)
            try:
                direct_amounts = self.router.functions.getAmountsOut(
                    amount,
                    [usdc_e, usdc]
                ).call()
                
                quotes["direct"] = {
                    "path": ["USDC.e", "USDC"],
                    "input_amount": amount,
                    "output_amount": direct_amounts[1],
                    "rate": direct_amounts[1] / amount,
                    "price_impact_percent": ((1 - (direct_amounts[1] / amount)) * 100)
                }
            except Exception as e:
                logger.warning(f"Failed to get direct path quote: {str(e)}")
                quotes["direct"] = {"error": str(e)}

            # Get quote through USDT (USDC.e -> USDT -> USDC)
            try:
                indirect_amounts = self.router.functions.getAmountsOut(
                    amount,
                    [usdc_e, usdt, usdc]
                ).call()
                
                quotes["via_usdt"] = {
                    "path": ["USDC.e", "USDT", "USDC"],
                    "input_amount": amount,
                    "output_amount": indirect_amounts[2],
                    "rate": indirect_amounts[2] / amount,
                    "price_impact_percent": ((1 - (indirect_amounts[2] / amount)) * 100)
                }
            except Exception as e:
                logger.warning(f"Failed to get USDT path quote: {str(e)}")
                quotes["via_usdt"] = {"error": str(e)}

            # Find best route
            valid_quotes = {
                path: data for path, data in quotes.items() 
                if "error" not in data
            }
            
            if not valid_quotes:
                raise ValueError("No valid swap routes found")
                
            best_route = max(
                valid_quotes.items(),
                key=lambda x: x[1]["output_amount"]
            )
            
            return {
                "quotes": quotes,
                "best_route": {
                    "path": best_route[0],
                    "details": best_route[1]
                },
                "input_token": {
                    "symbol": "USDC.e",
                    "address": usdc_e,
                    "amount": amount,
                    "amount_readable": amount / 1_000_000
                },
                "recommended_slippage": 0.5,  # Base slippage recommendation
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            logger.error(f"Failed to get swap quotes: {str(e)}")
            raise ValueError(f"Quote fetching failed: {str(e)}")

    async def execute_swap(self, amount: int, slippage_percent: float = 0.5) -> dict:
        """
        Execute USDC.e to USDC swap with enhanced debugging and timeout handling
        """
        try:
            logger.info("=== Starting Swap Execution ===")
            logger.info(f"Input: {amount} USDC.e units ({amount/1_000_000} USDC.e)")
            
            # Step 1: Get quotes and determine best route
            logger.info("Step 1: Fetching quotes...")
            quotes = await self.get_swap_quote(amount)
            if not quotes["quotes"]:
                raise ValueError("No valid swap routes available")
                
            best_route = quotes["best_route"]
            route_details = best_route["details"]
            logger.info(f"Selected route: {best_route['path']}")
            logger.info(f"Expected price impact: {route_details['price_impact_percent']}%")
            
            # Step 2: Set up swap parameters
            logger.info("Step 2: Setting up swap parameters...")
            usdc_e = Web3.to_checksum_address(USDC_ADDRESS)
            usdc = Web3.to_checksum_address("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
            usdt = Web3.to_checksum_address("0xc2132D05D31c914a87C6611C10748AEb04B58e8F")
            
            path = (
                [usdc_e, usdt, usdc] 
                if best_route["path"] == "via_usdt" 
                else [usdc_e, usdc]
            )
            logger.info(f"Path: {' -> '.join([addr[:6] + '...' + addr[-4:] for addr in path])}")
            
            # Step 3: Check current balances
            logger.info("Step 3: Checking balances...")
            initial_usdc_e_balance = self.usdc.functions.balanceOf(self.wallet_address).call()
            logger.info(f"Initial USDC.e balance: {initial_usdc_e_balance/1_000_000}")
            
            if initial_usdc_e_balance < amount:
                raise ValueError(f"Insufficient USDC.e balance. Have: {initial_usdc_e_balance/1_000_000}, Need: {amount/1_000_000}")
            
            # Step 4: Calculate minimum output with slippage
            expected_output = route_details["output_amount"]
            min_output = int(expected_output * (1 - slippage_percent / 100))
            logger.info(f"Expected output: {expected_output/1_000_000} USDC")
            logger.info(f"Minimum output: {min_output/1_000_000} USDC")
            
            # Step 5: Handle approval with timeout
            logger.info("Step 5: Checking and handling approval...")
            try:
                async with asyncio.timeout(30):  # 30 second timeout for approval
                    current_allowance = self.usdc.functions.allowance(
                        self.wallet_address,
                        self.QUICKSWAP_ROUTER
                    ).call()
                    logger.info(f"Current allowance: {current_allowance/1_000_000} USDC.e")
                    
                    if current_allowance < amount:
                        logger.info("Insufficient allowance, initiating approval...")
                        await self.approve_token(
                            token_contract=self.usdc,
                            spender_address=self.QUICKSWAP_ROUTER,
                            amount=amount
                        )
                        logger.info("Approval completed")
                    else:
                        logger.info("Sufficient allowance exists")
            except asyncio.TimeoutError:
                raise ValueError("Approval process timed out after 30 seconds")
            
            # Step 6: Build swap transaction with aggressive gas settings
            logger.info("Step 6: Building swap transaction...")
            deadline = int(time.time()) + 300  # Reduced to 5 minutes
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 100_000_000_000  # Increased to 100 gwei for faster inclusion
            max_fee = base_fee * 5 + priority_fee  # Increased multiplier
            
            swap_txn = self.router.functions.swapExactTokensForTokens(
                amount,
                min_output,
                path,
                self.wallet_address,
                deadline
            ).build_transaction({
                'chainId': 137,
                'gas': 300000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address
            })
            
            # Step 7: Execute transaction with timeout
            logger.info("Step 7: Executing swap transaction...")
            try:
                async with asyncio.timeout(60):  # 60 second timeout for transaction
                    signed_txn = self.w3.eth.account.sign_transaction(swap_txn, PRIVATE_KEY)
                    tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                    logger.info(f"Transaction sent: {tx_hash.hex()}")
                    
                    # Wait for receipt with progress logging
                    start_time = time.time()
                    while True:
                        try:
                            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                            if receipt is not None:
                                break
                        except Exception:
                            if time.time() - start_time > 45:  # Log every 45 seconds
                                logger.info("Still waiting for transaction receipt...")
                        await asyncio.sleep(3)
                    
                    if receipt['status'] != 1:
                        raise ValueError("Swap transaction failed")
                    logger.info("Transaction confirmed successfully")
                    
            except asyncio.TimeoutError:
                raise ValueError("Transaction execution timed out after 60 seconds")
            
            # Step 8: Verify final balance
            logger.info("Step 8: Verifying final balance...")
            native_usdc = self.w3.eth.contract(
                address=usdc,
                abi=USDC_ABI
            )
            final_usdc_balance = native_usdc.functions.balanceOf(self.wallet_address).call()
            logger.info(f"Final USDC balance: {final_usdc_balance/1_000_000}")
            
            return {
                "success": True,
                "transaction_hash": receipt['transactionHash'].hex(),
                "gas_used": receipt['gasUsed'],
                "route_used": {
                    "path": route_details['path'],
                    "price_impact": route_details['price_impact_percent']
                },
                "amounts": {
                    "input": {
                        "base_units": amount,
                        "usdc": amount / 1_000_000
                    },
                    "output": {
                        "expected": {
                            "base_units": expected_output,
                            "usdc": expected_output / 1_000_000
                        },
                        "minimum": {
                            "base_units": min_output,
                            "usdc": min_output / 1_000_000
                        },
                        "actual": {
                            "base_units": final_usdc_balance,
                            "usdc": final_usdc_balance / 1_000_000
                        }
                    }
                }
            }
            
        except Exception as e:
            logger.error(f"Swap execution failed: {str(e)}")
            raise ValueError(f"Failed to execute swap: {str(e)}")