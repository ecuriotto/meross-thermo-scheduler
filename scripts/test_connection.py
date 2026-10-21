#!/usr/bin/env python3
"""
Diagnostic script for Meross Thermostats (MTS200B and MTS205B).
Discovers online devices, inspects abilities, queries schedule schemas,
and outputs a complete raw dump to scripts/schedule_dump.json.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager


# ANSI styling
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


async def query_namespace(manager: MerossManager, device, namespace: str, payloads: list[dict]):
    """Attempts GET command on namespace with a list of candidate payloads until one succeeds."""
    last_err = None
    for payload in payloads:
        try:
            res = await manager.async_execute_cmd(
                destination_device_uuid=device.uuid,
                method="GET",
                namespace=namespace,
                payload=payload,
                mqtt_hostname=device.mqtt_host,
                mqtt_port=device.mqtt_port,
                timeout=5.0,
            )
            if res:
                return {"payload_used": payload, "response": res, "success": True}
        except Exception as e:
            last_err = str(e)
    return {"payload_used": None, "error": last_err, "success": False}


async def main():
    # Load .env from workspace root
    root_dir = Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    load_dotenv(dotenv_path=env_path)

    email = os.getenv("MEROSS_EMAIL")
    password = os.getenv("MEROSS_PASSWORD")
    api_base_url = os.getenv("MEROSS_API_BASE_URL", "https://iotx-eu.meross.com").rstrip("/")

    print(f"{BOLD}{BLUE}===================================================={RESET}")
    print(f"{BOLD}{BLUE}  Meross Thermostat Discovery & Diagnostic Tool     {RESET}")
    print(f"{BOLD}{BLUE}===================================================={RESET}\n")

    if not email or not password:
        print(f"{RED}[ERROR] Credentials missing!{RESET}")
        print("Please copy .env.example to .env and configure MEROSS_EMAIL and MEROSS_PASSWORD:")
        print(f"  cp {env_path.name}.example {env_path.name}")
        sys.exit(1)

    print(f"{CYAN}Connecting to Meross Cloud API:{RESET} {api_base_url}")
    print(f"{CYAN}Account Email:{RESET} {email}\n")

    http_client = None
    manager = None

    try:
        # Step 1: Authenticate against EU endpoint
        print(f"[*] Authenticating with Meross HTTP API...")
        http_client = await MerossHttpClient.async_from_user_password(
            api_base_url=api_base_url,
            email=email,
            password=password,
        )
        print(f"{GREEN}[✓] Authenticated successfully.{RESET}\n")

        # Step 2: Initialize Manager and discover devices
        print(f"[*] Initializing MerossManager and discovering devices over MQTT...")
        manager = MerossManager(http_client=http_client)
        await manager.async_init()
        await manager.async_device_discovery()

        all_devices = manager.find_devices()
        print(f"{GREEN}[✓] Discovered {len(all_devices)} device(s) in total.{RESET}\n")

        thermostat_candidates = []
        for dev in all_devices:
            dev_type = (dev.type or "").lower()
            dev_name = (dev.name or "").lower()
            is_thermostat = "mts200" in dev_type or "mts205" in dev_type or "thermostat" in dev_type or "thermostat" in dev_name
            thermostat_candidates.append((dev, is_thermostat))

        print(f"{BOLD}Device List:{RESET}")
        for dev, is_t in thermostat_candidates:
            star = f"{YELLOW}[THERMOSTAT TARGET]{RESET} " if is_t else ""
            status_color = GREEN if dev.online_status.name.lower() == "online" else RED
            print(f"  • {star}{BOLD}{dev.name}{RESET} (Type: {dev.type}, UUID: {dev.uuid})")
            print(f"    Online Status: {status_color}{dev.online_status.name}{RESET}")
            print(f"    MQTT Host: {dev.mqtt_host}:{dev.mqtt_port}")

        print()

        dump_data = {
            "api_base_url": api_base_url,
            "devices": {},
        }

        # Step 3: Deep inspection of thermostats (or all devices if no specific thermostat model is flagged)
        targets = [dev for dev, is_t in thermostat_candidates if is_t]
        if not targets:
            print(f"{YELLOW}[!] No devices specifically matching 'mts200' or 'mts205' found. Inspecting all devices...{RESET}")
            targets = [dev for dev, _ in thermostat_candidates]

        for device in targets:
            print(f"{BOLD}{CYAN}----------------------------------------------------{RESET}")
            print(f"{BOLD}Inspecting Device:{RESET} {device.name} ({device.uuid})")
            print(f"{BOLD}{CYAN}----------------------------------------------------{RESET}")

            dev_dump = {
                "name": device.name,
                "type": device.type,
                "uuid": device.uuid,
                "online_status": device.online_status.name,
                "mqtt_host": device.mqtt_host,
                "mqtt_port": device.mqtt_port,
                "matched_abilities": {},
                "all_abilities": list(device.abilities.keys()) if device.abilities else [],
                "commands": {},
            }

            abilities = device.abilities or {}
            # Filter namespaces of interest
            thermostat_and_schedule_abilities = {
                k: v for k, v in abilities.items()
                if any(kw in k.lower() for kw in ["thermostat", "schedule", "temperature", "mode"])
            }
            dev_dump["matched_abilities"] = thermostat_and_schedule_abilities

            print(f"\n{BOLD}Matching Abilities (Thermostat/Schedule/Mode):{RESET}")
            if thermostat_and_schedule_abilities:
                for ns in thermostat_and_schedule_abilities:
                    print(f"  - {ns}")
            else:
                print("  (None found)")

            # Check System.All
            print(f"\n[*] Querying Appliance.System.All...")
            all_res = await query_namespace(manager, device, "Appliance.System.All", [{}])
            dev_dump["commands"]["Appliance.System.All"] = all_res
            if all_res.get("success"):
                all_payload = all_res.get("response", {}).get("payload", {}).get("all", {})
                digest = all_payload.get("digest", {})
                thermostat_digest = digest.get("thermostat", {})
                print(f"{GREEN}[✓] Appliance.System.All retrieved successfully.{RESET}")
                if thermostat_digest:
                    print(f"    Thermostat Digest: {json.dumps(thermostat_digest, indent=4)}")
            else:
                print(f"{RED}[✗] Failed to query Appliance.System.All: {all_res.get('error')}{RESET}")

            # Check Thermostat Mode / ModeB
            for mode_ns in ["Appliance.Control.Thermostat.Mode", "Appliance.Control.Thermostat.ModeB"]:
                if mode_ns in abilities or not abilities:
                    print(f"\n[*] Querying {mode_ns}...")
                    mode_res = await query_namespace(
                        manager, device, mode_ns,
                        [{"mode": [{"channel": 0}]}, {"modeB": [{"channel": 0}]}, {"channel": 0}, {}]
                    )
                    dev_dump["commands"][mode_ns] = mode_res
                    if mode_res.get("success"):
                        print(f"{GREEN}[✓] {mode_ns} returned:{RESET}")
                        print(json.dumps(mode_res["response"], indent=2))
                    else:
                        print(f"{YELLOW}[-] {mode_ns} error: {mode_res.get('error')}{RESET}")

            # Check Thermostat Schedule namespaces based on device abilities
            supported_sched_ns = [
                ns for ns in ["Appliance.Control.Thermostat.Schedule", "Appliance.Control.Thermostat.ScheduleB"]
                if ns in abilities
            ]
            if not supported_sched_ns:
                # Fallback if abilities dictionary didn't contain either explicitly
                supported_sched_ns = ["Appliance.Control.Thermostat.Schedule"]

            for sched_ns in supported_sched_ns:
                print(f"\n[*] Querying {sched_ns} (supported by device abilities)...")
                is_b = "scheduleb" in sched_ns.lower()
                candidate_payloads = (
                    [{"scheduleB": [{"channel": 0}]}, {"channel": 0}, {}]
                    if is_b
                    else [{"schedule": [{"channel": 0}]}, {"channel": 0}, {}]
                )
                sched_res = await query_namespace(manager, device, sched_ns, candidate_payloads)
                dev_dump["commands"][sched_ns] = sched_res
                if sched_res.get("success"):
                    print(f"{GREEN}[✓] SUCCESS! {sched_ns} returned raw schedule payload:{RESET}")
                    print(json.dumps(sched_res["response"], indent=2))
                else:
                    print(f"{YELLOW}[-] {sched_ns} error: {sched_res.get('error')}{RESET}")

            dump_data["devices"][device.uuid] = dev_dump

        # Step 4: Write complete dump to JSON file
        out_file = root_dir / "scripts" / "schedule_dump.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, indent=2, ensure_ascii=False)

        print(f"\n{BOLD}{GREEN}===================================================={RESET}")
        print(f"{BOLD}{GREEN}[✓] Discovery complete! Raw dump saved to:{RESET}")
        print(f"    {out_file}")
        print(f"{BOLD}{GREEN}===================================================={RESET}\n")

    except Exception as e:
        print(f"\n{RED}[FATAL ERROR] {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Step 5: Clean teardown
        print("[*] Cleaning up connections...")
        if manager:
            try:
                manager.close()
            except Exception as e:
                print(f"Warning closing manager: {e}")
        if http_client:
            try:
                await http_client.async_logout()
            except Exception as e:
                print(f"Warning logging out: {e}")
        print("[*] Teardown complete. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())
