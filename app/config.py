# src/config.py
import os
from dotenv import load_dotenv
import logging
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIVATE_KEY = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("POLYGON_WALLET_PRIVATE_KEY not set in environment")

# Contract addresses
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_ADDRESS = "0x4bfb41d5B3570defd03c39a9A4d8de6bd8b8982e"
POLYGON_RPC = "https://polygon-rpc.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
GAMMA_MARKETS_ENDPOINT = f"{GAMMA_URL}/markets"
SUBGRAPH_URL = 'https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn'

# Contract ABIs
USDC_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}
]

# src/config.py
import os
from dotenv import load_dotenv
import logging
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIVATE_KEY = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("POLYGON_WALLET_PRIVATE_KEY not set in environment")
CHAIN_ID=137

# Contract addresses
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_ADDRESS = "0x4bfb41d5B3570defd03c39a9A4d8de6bd8b8982e"
ACROSS_SPOKE_POOL_ADDRESS= "0x9295ee1d8C5b022Be115A2AD3c30C72E34e7F096"
POLYGON_RPC = "https://polygon-rpc.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
GAMMA_MARKETS_ENDPOINT = f"{GAMMA_URL}/markets"
SUBGRAPH_URL = 'https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn'

# Contract ABIs
USDC_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}
]

CTF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "", "type": "bytes32"}],
        "name": "payoutDenominator",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "", "type": "bytes32"}],
        "name": "payoutNumerators",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "name": "getOutcomeSlotCount",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "operator", "type": "address"}
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"}
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

ACROSS_SPOKE_POOL_ABI = [
    {
        "inputs": [
            {"name": "depositor", "type": "address"},
            {"name": "recipient", "type": "address"},
            {"name": "inputToken", "type": "address"},
            {"name": "outputToken", "type": "address"},
            {"name": "inputAmount", "type": "uint256"},
            {"name": "outputAmount", "type": "uint256"},
            {"name": "destinationChainId", "type": "uint256"},
            {"name": "exclusiveRelayer", "type": "address"},
            {"name": "quoteTimestamp", "type": "uint32"},
            {"name": "fillDeadline", "type": "uint32"},
            {"name": "exclusivityDeadline", "type": "uint32"},
            {"name": "message", "type": "bytes"}
        ],
        "name": "depositV3",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getCurrentTime",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "depositQuoteTimeBuffer",
        "outputs": [{"name": "", "type": "uint32"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "fillDeadlineBuffer",
        "outputs": [{"name": "", "type": "uint32"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "inputToken", "type": "address"},
            {"indexed": False, "name": "outputToken", "type": "address"},
            {"indexed": False, "name": "inputAmount", "type": "uint256"},
            {"indexed": False, "name": "outputAmount", "type": "uint256"},
            {"indexed": True, "name": "destinationChainId", "type": "uint256"},
            {"indexed": True, "name": "depositId", "type": "uint32"},
            {"indexed": False, "name": "quoteTimestamp", "type": "uint32"},
            {"indexed": False, "name": "fillDeadline", "type": "uint32"},
            {"indexed": False, "name": "exclusivityDeadline", "type": "uint32"},
            {"indexed": True, "name": "depositor", "type": "address"},
            {"indexed": False, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "exclusiveRelayer", "type": "address"},
            {"indexed": False, "name": "message", "type": "bytes"}
        ],
        "name": "V3FundsDeposited",
        "type": "event"
    }
]