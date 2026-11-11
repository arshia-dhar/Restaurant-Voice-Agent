"""FastAPI wrapper around tools.py.   Run:  uvicorn main:app --reload   (docs at /docs)"""
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import tools
from tool_schemas import TOOL_SCHEMAS

app = FastAPI(title="Trattoria Bellavista voice-agent tools")


class Availability(BaseModel):
    date: str
    time: str
    party_size: int


class Reservation(Availability):
    guest_name: str
    phone: str


class Lookup(BaseModel):
    guest_name: str
    date: str


class Cancel(BaseModel):
    reservation_id: str
    phone: str


class Allergen(BaseModel):
    item_name: Optional[str] = None
    allergen: Optional[str] = None


class MenuQuery(BaseModel):
    category: Optional[str] = None
    name: Optional[str] = None
    max_price: Optional[float] = None


class Handoff(BaseModel):
    reason: str
    context: str = ""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tools/schemas")
def schemas():
    return TOOL_SCHEMAS


@app.post("/tools/check_availability")
def check_availability(b: Availability):
    return tools.check_availability(**b.model_dump())


@app.post("/tools/propose_booking")
def propose_booking(b: Reservation):
    return tools.propose_booking(**b.model_dump())


@app.post("/tools/create_reservation")   # note: the confirmation gate lives in agent.py, not here
def create_reservation(b: Reservation):
    return tools.create_reservation(**b.model_dump())


@app.post("/tools/lookup_reservation")
def lookup_reservation(b: Lookup):
    return tools.lookup_reservation(**b.model_dump())


@app.post("/tools/cancel_reservation")
def cancel_reservation(b: Cancel):
    return tools.cancel_reservation(**b.model_dump())


@app.post("/tools/search_menu_allergens")
def search_menu_allergens(b: Allergen):
    return tools.search_menu_allergens(**b.model_dump())


@app.post("/tools/search_menu")
def search_menu(b: MenuQuery):
    return tools.search_menu(**b.model_dump())


@app.post("/tools/handoff_to_human")
def handoff_to_human(b: Handoff):
    return tools.handoff_to_human(**b.model_dump())


@app.post("/tools/{name}")
def unknown(name: str):
    raise HTTPException(404, f"No such tool: {name}")