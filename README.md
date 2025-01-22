# Market Agent Sever

Prediction markets should be available to everyone everywhere. Use this agent to place orders and manage positions on your behalf and liberate yourself from the tyranny of Polymarket's API key.  

## What is Working:

- FastAPI - server for letting user open positons
- CLOB Client - Opens, sells, and redeems positions on Polymarket 
- Local Postgres Server - tracking positions

## TODO:
- Keygen - Use a private mnemonic to cycle through addresses
- Address Checking - Create and manage API keys for new address
- VPN management - Set up and cycle ip addresses
- TEE Deployment - Dockerize the server and configure for trusted excution enviroment


# Contributing

If you'd like to use something like this or want to see it developed email ben@okaybet.fun. 

## Getting Started Locally

```
# start a vitual env
source venv/bin/activate

# Install from requirements.txt
pip install -r requirements.txt
```

## To run the server:
```
# From the root directory (where requirements.txt is)
uvicorn app.main:app --reload
```
