from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging
from sqlalchemy import text
from .api.routes.health import router as health_router
from .api.routes.status import router as status_router
from .api.routes.positions import router as positions_router
from .api.routes.orders import router as orders_router
from .api.routes.delegated_orders import router as delegated_orders_router
from .api.routes.delegated_sell import router as delegated_sell_router
from .api.routes.resolution import router as resolution_router
from .api.routes.bridge import router as bridge_router
from .api.routes.swap import router as swap_router
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
app = FastAPI(title="Market Agent Server")

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
        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            conn.execute(text("""
                ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_condition_id_fkey;
                ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_condition_id_fkey;
            """))

            conn.execute(text("""
                ALTER TABLE markets ALTER COLUMN condition_id TYPE varchar(256);
                ALTER TABLE markets ADD COLUMN IF NOT EXISTS token_id varchar(256);
            """))

            conn.execute(text("""
                ALTER TABLE positions 
                    ADD CONSTRAINT positions_condition_id_fkey 
                    FOREIGN KEY (condition_id) REFERENCES markets(condition_id);

                ALTER TABLE transactions 
                    ADD CONSTRAINT transactions_condition_id_fkey 
                    FOREIGN KEY (condition_id) REFERENCES markets(condition_id);
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_markets_token_id ON markets(token_id);
                CREATE INDEX IF NOT EXISTS ix_positions_order ON positions(order_id);
            """))

            conn.execute(text("""
                ALTER TABLE positions
                ADD COLUMN IF NOT EXISTS order_id varchar(66);
            """))

            conn.commit()

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
app.include_router(bridge_router)
app.include_router(swap_router)
app.include_router(resolution_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)