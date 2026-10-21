"""
Meross Service layer managing connection lifecycle, device discovery,
and thermostat schedule reading/writing.
"""

import logging
import os
from typing import List, Optional, Dict, Any

from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager

from app.schemas import (
    DeviceSummary,
    DeviceScheduleResponse,
    WeekSchedule,
    raw_payload_to_week_schedule,
    week_schedule_to_raw_payload,
)

from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded from workspace root
root_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=root_env)

logger = logging.getLogger("meross_service")


class MerossService:
    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        api_base_url: Optional[str] = None,
    ):
        self.email = email or os.getenv("MEROSS_EMAIL")
        self.password = password or os.getenv("MEROSS_PASSWORD")
        self.api_base_url = (api_base_url or os.getenv("MEROSS_API_BASE_URL", "https://iotx-eu.meross.com")).rstrip("/")

        if not self.email or not self.password:
            raise ValueError("MEROSS_EMAIL and MEROSS_PASSWORD must be provided or configured in .env")

        self.http_client: Optional[MerossHttpClient] = None
        self.manager: Optional[MerossManager] = None
        self._is_started = False

    async def start(self) -> None:
        """Initializes the HTTP client, manager, and discovers devices."""
        if self._is_started:
            return

        logger.info(f"Authenticating against {self.api_base_url} as {self.email}...")
        self.http_client = await MerossHttpClient.async_from_user_password(
            api_base_url=self.api_base_url,
            email=self.email,
            password=self.password,
        )

        logger.info("Initializing MerossManager...")
        self.manager = MerossManager(http_client=self.http_client)
        await self.manager.async_init()

        logger.info("Running initial device discovery...")
        await self.manager.async_device_discovery()
        self._is_started = True
        logger.info("MerossService started successfully.")

    async def stop(self) -> None:
        """Cleanly closes manager and logs out from HTTP client."""
        logger.info("Stopping MerossService...")
        if self.manager:
            try:
                self.manager.close()
            except Exception as e:
                logger.warning(f"Error closing manager: {e}")
            self.manager = None

        if self.http_client:
            try:
                await self.http_client.async_logout()
            except Exception as e:
                logger.warning(f"Error logging out HTTP client: {e}")
            self.http_client = None

        self._is_started = False
        logger.info("MerossService stopped.")

    def _ensure_started(self) -> None:
        if not self._is_started or not self.manager:
            raise RuntimeError("MerossService is not started. Call start() first.")

    def get_device(self, uuid: str):
        """Finds a device by UUID or raises KeyError."""
        self._ensure_started()
        devices = self.manager.find_devices(device_uuids=[uuid])
        if not devices:
            # Fallback search all
            for dev in self.manager.find_devices():
                if dev.uuid == uuid:
                    return dev
            raise KeyError(f"Device with UUID '{uuid}' not found.")
        return devices[0]

    def _get_supported_schedule_namespace(self, device) -> str:
        """Determines whether the device uses Schedule or ScheduleB."""
        abilities = device.abilities or {}
        if "Appliance.Control.Thermostat.Schedule" in abilities:
            return "Appliance.Control.Thermostat.Schedule"
        if "Appliance.Control.Thermostat.ScheduleB" in abilities:
            return "Appliance.Control.Thermostat.ScheduleB"
        return "Appliance.Control.Thermostat.Schedule"

    async def get_device_summary(self, device) -> DeviceSummary:
        """Fetches the current status (temp, target, mode) for a device."""
        online = getattr(device.online_status, "name", str(device.online_status)).lower() == "online"

        summary = DeviceSummary(
            uuid=device.uuid,
            name=device.name or "Unknown Thermostat",
            model=device.type or "Unknown Model",
            online=online,
        )

        if not online:
            return summary

        # Query Thermostat Mode for temps and status
        try:
            mode_resp = await self.manager.async_execute_cmd(
                destination_device_uuid=device.uuid,
                method="GET",
                namespace="Appliance.Control.Thermostat.Mode",
                payload={"mode": [{"channel": 0}]},
                mqtt_hostname=device.mqtt_host,
                mqtt_port=device.mqtt_port,
                timeout=5.0,
            )
            mode_list = mode_resp.get("mode", [])
            if mode_list:
                m = mode_list[0]
                summary.current_temp = round(m.get("currentTemp", 0) / 10.0, 1)
                summary.target_temp = round(m.get("targetTemp", 0) / 10.0, 1)
                summary.mode = m.get("mode")
                summary.is_heating = (m.get("state") == 1)
                summary.heat_temp = round(m.get("heatTemp", 0) / 10.0, 1) if "heatTemp" in m else None
                summary.cool_temp = round(m.get("coolTemp", 0) / 10.0, 1) if "coolTemp" in m else None
                summary.eco_temp = round(m.get("ecoTemp", 0) / 10.0, 1) if "ecoTemp" in m else None
                summary.manual_temp = round(m.get("manualTemp", 0) / 10.0, 1) if "manualTemp" in m else None
        except Exception as e:
            logger.warning(f"Could not fetch mode for {device.name} ({device.uuid}): {e}")

        return summary

    async def get_all_thermostats(self) -> List[DeviceSummary]:
        """Discovers and returns summaries for all online thermostats."""
        self._ensure_started()
        devices = self.manager.find_devices()
        results: List[DeviceSummary] = []
        for dev in devices:
            dev_type = (dev.type or "").lower()
            abilities = dev.abilities or {}
            is_thermostat = (
                "mts200" in dev_type
                or "mts205" in dev_type
                or "Appliance.Control.Thermostat.Schedule" in abilities
                or "Appliance.Control.Thermostat.Mode" in abilities
            )
            if is_thermostat:
                summary = await self.get_device_summary(dev)
                results.append(summary)
        return results

    async def get_schedule(self, uuid: str) -> DeviceScheduleResponse:
        """Fetches and parses the current schedule for a thermostat."""
        device = self.get_device(uuid)
        ns = self._get_supported_schedule_namespace(device)

        logger.info(f"Querying schedule for {device.name} via {ns}...")
        raw_res = await self.manager.async_execute_cmd(
            destination_device_uuid=device.uuid,
            method="GET",
            namespace=ns,
            payload={"schedule": [{"channel": 0}]},
            mqtt_hostname=device.mqtt_host,
            mqtt_port=device.mqtt_port,
            timeout=8.0,
        )

        schedule = raw_payload_to_week_schedule(raw_res)
        summary = await self.get_device_summary(device)

        return DeviceScheduleResponse(
            device=summary,
            schedule=schedule,
            raw_schedule=raw_res,
        )

    async def set_schedule(self, uuid: str, schedule: WeekSchedule) -> DeviceScheduleResponse:
        """
        Validates, serializes, and pushes a new 7-day schedule to a thermostat.
        """
        device = self.get_device(uuid)
        ns = self._get_supported_schedule_namespace(device)

        raw_payload = week_schedule_to_raw_payload(schedule, channel=0, section=8)
        logger.info(f"Sending SET schedule to {device.name} ({uuid}) via {ns}...")

        set_res = await self.manager.async_execute_cmd(
            destination_device_uuid=device.uuid,
            method="SET",
            namespace=ns,
            payload=raw_payload,
            mqtt_hostname=device.mqtt_host,
            mqtt_port=device.mqtt_port,
            timeout=10.0,
        )
        logger.info(f"SET response from {device.name}: {set_res}")

        # Fetch and return the verified updated schedule
        return await self.get_schedule(uuid)

    async def set_mode(self, uuid: str, mode: int) -> DeviceSummary:
        """
        Switches between Schedule (mode=3) and Manual (mode=4).
        """
        device = self.get_device(uuid)
        logger.info(f"Setting mode {mode} on {device.name} ({uuid})...")
        payload = {"mode": [{"channel": 0, "mode": mode}]}
        await self.manager.async_execute_cmd(
            destination_device_uuid=device.uuid,
            method="SET",
            namespace="Appliance.Control.Thermostat.Mode",
            payload=payload,
            mqtt_hostname=device.mqtt_host,
            mqtt_port=device.mqtt_port,
            timeout=8.0,
        )
        return await self.get_device_summary(device)

    async def set_target_temperature(self, uuid: str, target_temp: float) -> DeviceSummary:
        """
        Sets the manual target temperature on a thermostat (switches to Manual mode=4 if not already).
        """
        device = self.get_device(uuid)
        temp_x10 = int(round(target_temp * 10))
        logger.info(f"Setting manual target temperature {target_temp}°C ({temp_x10}) on {device.name} ({uuid})...")
        payload = {
            "mode": [
                {
                    "channel": 0,
                    "mode": 4,  # Manual mode
                    "manualTemp": temp_x10,
                    "targetTemp": temp_x10,
                }
            ]
        }
        await self.manager.async_execute_cmd(
            destination_device_uuid=device.uuid,
            method="SET",
            namespace="Appliance.Control.Thermostat.Mode",
            payload=payload,
            mqtt_hostname=device.mqtt_host,
            mqtt_port=device.mqtt_port,
            timeout=8.0,
        )
        return await self.get_device_summary(device)

    async def clone_schedule(self, source_uuid: str, target_uuid: str) -> DeviceScheduleResponse:
        """
        Clones the full weekly schedule from source thermostat to target thermostat.
        """
        logger.info(f"Cloning schedule from {source_uuid} to {target_uuid}...")
        source_data = await self.get_schedule(source_uuid)
        return await self.set_schedule(target_uuid, source_data.schedule)

