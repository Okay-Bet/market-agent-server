# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging

# Fix imports to use relative paths
from .api.routes.health import router as health_router
from .api.routes.status import router as status_router
from .api.routes.positions import router as positions_router
from .api.routes.orders import router as orders_router
from .api.routes.delegated_orders import router as delegated_orders_router

# Setup logging and env variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Polymarket Trading Server")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(status_router)
app.include_router(positions_router)
app.include_router(orders_router)
app.include_router(delegated_orders_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)