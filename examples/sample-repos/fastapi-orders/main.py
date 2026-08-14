from fastapi import FastAPI, APIRouter, Depends, Security, Query
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

app = FastAPI(title="Orders Service", version="2.1.0")
router = APIRouter(prefix="/orders")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class OrderCreate(BaseModel):
    product_id: int
    quantity: int


def current_user(token: str = Depends(oauth2_scheme)):
    return {"token": token}


@router.get("")
def list_orders(status: str = Query(default="open"), limit: int = 20):
    """List orders filtered by status."""
    return []


@router.get("/{order_id}")
def get_order(order_id: int):
    """Retrieve a single order."""
    return {"id": order_id}


@router.post("")
def create_order(payload: OrderCreate, user=Security(current_user)):
    """Create a new order (requires an authenticated user)."""
    return {"id": 1, **payload.dict()}


app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
