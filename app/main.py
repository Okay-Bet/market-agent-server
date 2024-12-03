from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SignatureRequest(BaseModel):
    address: str
    signature: str
    timestamp: str
    nonce: int = 0

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/api/credentials")
async def create_or_derive_credentials(
    poly_address: str = Header(..., alias="POLY_ADDRESS"),
    poly_signature: str = Header(..., alias="POLY_SIGNATURE"),
    poly_timestamp: str = Header(..., alias="POLY_TIMESTAMP"),
    poly_nonce: int = Header(0, alias="POLY_NONCE")
):
    try:
        client = ClobClient(
            "https://clob.polymarket.com",
            chain_id=137
        )
        
        # Forward the L1 auth headers to the CLOB API
        client.set_auth_headers({
            "POLY_ADDRESS": poly_address,
            "POLY_SIGNATURE": poly_signature,
            "POLY_TIMESTAMP": poly_timestamp,
            "POLY_NONCE": str(poly_nonce)
        })
        
        try:
            # Try to derive first
            creds = client.derive_api_key()
        except:
            # If deriving fails, create new
            creds = client.create_api_key()
            
        return {
            "success": True,
            "credentials": {
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )