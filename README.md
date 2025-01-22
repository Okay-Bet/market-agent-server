# Market Agent Sever

Prediction markets should be available to everyone. Use this agent to place orders and manage positions on your behalf.  

## How it works:

- FastAPI - python server for letting user open positons
- Local Postgres Server - tracking positions
- Keygen - Use a private mnemonic to cycle through addresses
- VPN management  


## Getting Started

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
