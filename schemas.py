from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal

app = FastAPI()

# Temporary mock database
mock_db = {
"LIBRARY_01": {
        "name": "Sentra Court",
        "capacity": 50,
        "current_occupancy": 5,
        "wifi": True,
        "power": True
    },
    "LIBRARY_02":{
        "name" : "Apaya",
        "capacity": 60,
        "current_occupancy": 5,
        "wifi": True,
        "power": True,
    }
}

class SensorEvent(BaseModel):
    # Using Field alias to support "area id" with a space as defined in the spec
    area_id : str = Field(... , alias = "area id")
    event : Literal["ENTER" , "EXIT"]

