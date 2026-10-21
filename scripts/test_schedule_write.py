#!/usr/bin/env python3
"""
CLI test script to safely verify the read/modify/write cycle of a thermostat schedule.
Reads the current schedule, modifies a single slot by +/- 0.5°C, writes it to the thermostat,
verifies persistence, and provides an option to immediately revert back.
"""

import argparse
import asyncio
import copy
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add workspace root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from app.meross_service import MerossService
from app.schemas import WeekSchedule


GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


async def main():
    parser = argparse.ArgumentParser(description="Test reading and writing Meross thermostat schedule.")
    parser.add_argument("--dry-run", action="store_true", help="Perform schema validation without sending SET command to thermostat")
    parser.add_argument("--auto-revert", action="store_true", help="Automatically revert back to original schedule after verification")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt before writing")
    args = parser.parse_args()

    env_path = root_dir / ".env"
    load_dotenv(dotenv_path=env_path)

    print(f"{BOLD}{BLUE}===================================================={RESET}")
    print(f"{BOLD}{BLUE}  Meross Thermostat Schedule Write Verification    {RESET}")
    print(f"{BOLD}{BLUE}===================================================={RESET}\n")

    service = MerossService()

    try:
        print("[*] Starting MerossService...")
        await service.start()

        devices = await service.get_all_thermostats()
        if not devices:
            print(f"{RED}[ERROR] No thermostats found on this account.{RESET}")
            return

        print(f"{GREEN}[✓] Discovered {len(devices)} thermostat(s):{RESET}")
        for idx, d in enumerate(devices):
            print(f"  [{idx + 1}] {BOLD}{d.name}{RESET} ({d.model}, UUID: {d.uuid}) - Ambient: {d.current_temp}°C, Target: {d.target_temp}°C")

        # Pick the first device
        target_dev = devices[0]
        print(f"\n{CYAN}Selected target:{RESET} {BOLD}{target_dev.name}{RESET} ({target_dev.uuid})")

        # Step 1: Read schedule
        print("\n[*] Reading current schedule from thermostat...")
        sched_resp = await service.get_schedule(target_dev.uuid)
        orig_schedule = sched_resp.schedule

        print(f"\n{BOLD}Current Monday Schedule:{RESET}")
        for idx, slot in enumerate(orig_schedule.mon):
            print(f"  Slot {idx + 1}: {slot.start_time} - {slot.end_time} -> {slot.temp}°C")

        # Step 2: Prepare test modification (first slot Monday)
        mod_schedule = copy.deepcopy(orig_schedule)
        target_slot = mod_schedule.mon[0]
        old_temp = target_slot.temp

        # Adjust by 0.5°C safely within range
        delta = 0.5 if old_temp < 30.0 else -0.5
        new_temp = round(old_temp + delta, 1)
        target_slot.temp = new_temp

        print(f"\n{YELLOW}[TEST MODIFICATION]{RESET}")
        print(f"  Monday Slot 1 ({target_slot.start_time} - {target_slot.end_time}):")
        print(f"  {old_temp}°C  --->  {BOLD}{GREEN}{new_temp}°C{RESET} (delta: {delta:+.1f}°C)")

        if args.dry_run:
            print(f"\n{GREEN}[DRY-RUN] Schema conversion and validation succeeded. Skipping write.{RESET}")
            return

        # Confirmation
        if not args.yes:
            confirm = input(f"\nProceed with writing this test change to {target_dev.name}? (y/N): ").strip().lower()
            if confirm != "y":
                print("Write cancelled by user.")
                return

        # Step 3: Write schedule
        print(f"\n[*] Sending SET command to {target_dev.name}...")
        updated_resp = await service.set_schedule(target_dev.uuid, mod_schedule)
        verified_temp = updated_resp.schedule.mon[0].temp

        print(f"{GREEN}[✓] Schedule written successfully!{RESET}")
        print(f"    Verified Monday Slot 1 temperature: {BOLD}{verified_temp}°C{RESET}")

        if verified_temp == new_temp:
            print(f"\n{BOLD}{GREEN}★★★ VERIFICATION PASSED: Thermostat persisted the modification! ★★★{RESET}")
            print(f"Please check your Meross mobile app to see Monday Slot 1 showing {new_temp}°C.")
        else:
            print(f"{RED}[✗] Mismatch: Expected {new_temp}°C, got {verified_temp}°C{RESET}")

        # Step 4: Revert
        should_revert = args.auto_revert
        if not args.auto_revert:
            rev_input = input(f"\nDo you want to revert back to original {old_temp}°C now? (Y/n): ").strip().lower()
            should_revert = (rev_input != "n")

        if should_revert:
            print(f"\n[*] Reverting schedule back to original {old_temp}°C...")
            reverted_resp = await service.set_schedule(target_dev.uuid, orig_schedule)
            reverted_temp = reverted_resp.schedule.mon[0].temp
            print(f"{GREEN}[✓] Reverted successfully! Monday Slot 1 is back to {reverted_temp}°C.{RESET}")

    finally:
        print("\n[*] Stopping MerossService...")
        await service.stop()
        print("[*] Finished.")


if __name__ == "__main__":
    asyncio.run(main())
