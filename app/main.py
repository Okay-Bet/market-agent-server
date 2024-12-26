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
from .api.routes.delegated_sell import router as delegated_sell_router
from .api.routes.resolution import router as resolution_router 
from .services.web3_service import Web3Service
from .services.postgres_service import PostgresService
from .services.market_resolution import MarketResolutionService
from .models.db import Base
from .database import engine

# Setup logging and env variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize services
web3_service = Web3Service()
postgres_service = PostgresService()
market_resolution_service = MarketResolutionService(web3_service, postgres_service)

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

@app.on_event("startup")
async def startup_event():
    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to start services: {str(e)}")
        raise

# Include routers
app.include_router(health_router)
app.include_router(status_router)
app.include_router(positions_router)
app.include_router(orders_router)
app.include_router(delegated_orders_router)
app.include_router(delegated_sell_router)
app.include_router(resolution_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)