from fastapi import APIRouter
from .routes.health import router as health_router
from .routes.status import router as status_router
from .routes.positions import router as positions_router
from .routes.orders import router as orders_router
from .routes.bridge import router as bridge_router
from .routes.swap import router as swap_router
from .routes.resolve import router as resolve_router

router = APIRouter()

router.include_router(health_router)
router.include_router(status_router)
router.include_router(positions_router)
router.include_router(orders_router)
router.include_router(resolve_router)