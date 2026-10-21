"""
FastAPI application serving REST APIs and the thermostat scheduler frontend.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.meross_service import MerossService
from app.schemas import DeviceSummary, DeviceScheduleResponse, WeekSchedule, SetModeRequest, SetTargetTempRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager maintaining a single persistent Meross connection
    across the entire application lifetime.
    """
    logger.info("Initializing MerossService during FastAPI startup...")
    service = MerossService()
    await service.start()
    app.state.meross_service = service
    try:
        yield
    finally:
        logger.info("Tearing down MerossService during FastAPI shutdown...")
        await service.stop()


app = FastAPI(
    title="Meross Thermostat Smart Scheduler",
    description="Smart schedule manager for Meross MTS200B and MTS205B Wi-Fi thermostats",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_service(request: Request) -> MerossService:
    service: MerossService = getattr(request.app.state, "meross_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Meross service is not available.")
    return service


@app.get("/api/devices", response_model=List[DeviceSummary], summary="List all discovered thermostats")
async def list_devices(service: MerossService = Depends(get_service)):
    try:
        devices = await service.get_all_thermostats()
        return devices
    except Exception as e:
        logger.exception("Error listing devices")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/devices/{uuid}/schedule", response_model=DeviceScheduleResponse, summary="Get 7-day schedule for a thermostat")
async def get_schedule(uuid: str, service: MerossService = Depends(get_service)):
    try:
        schedule = await service.get_schedule(uuid)
        return schedule
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error fetching schedule for {uuid}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/{uuid}/schedule", response_model=DeviceScheduleResponse, summary="Save weekly schedule to a thermostat")
async def save_schedule(
    uuid: str,
    schedule: WeekSchedule = Body(..., description="7-day weekly schedule"),
    service: MerossService = Depends(get_service)
):
    try:
        updated = await service.set_schedule(uuid, schedule)
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error saving schedule for {uuid}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/{uuid}/clone-to/{target_uuid}", response_model=DeviceScheduleResponse, summary="Clone full weekly schedule to target thermostat")
async def clone_schedule(uuid: str, target_uuid: str, service: MerossService = Depends(get_service)):
    try:
        updated = await service.clone_schedule(uuid, target_uuid)
        return updated
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error cloning schedule from {uuid} to {target_uuid}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/{uuid}/mode", response_model=DeviceSummary, summary="Switch thermostat mode (3 for Schedule, 4 for Manual)")
async def set_device_mode(
    uuid: str,
    req: SetModeRequest = Body(...),
    service: MerossService = Depends(get_service)
):
    try:
        updated = await service.set_mode(uuid, req.mode)
        return updated
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error setting mode for {uuid}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/{uuid}/target-temperature", response_model=DeviceSummary, summary="Set manual target temperature on thermostat")
async def set_target_temperature(
    uuid: str,
    req: SetTargetTempRequest = Body(...),
    service: MerossService = Depends(get_service)
):
    try:
        updated = await service.set_target_temperature(uuid, req.target_temp)
        return updated
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error setting target temperature for {uuid}")
        raise HTTPException(status_code=500, detail=str(e))


# Static files mount
static_dir = Path(__file__).resolve().parent.parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def serve_index():
    index_file = static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(index_file)
