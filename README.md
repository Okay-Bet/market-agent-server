# polymarket-clob-server
proxy server in python to allow users to make prediction market positions. 


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
