# Development Plan: Meross Scheduler

> **Instruction for AI Agent:** Execute this plan strictly **one phase at a time**. Do NOT write code for future phases until the current phase is tested and confirmed by the user. Update the checkboxes `[x]` in this file as tasks are completed.

## Phase 1: Environment Setup & Read-Only Connection Test (Discovery)
**Goal:** Verify credentials, discover the `mts200b` and `mts205b` thermostats, and dump their exact raw JSON schedule schema without modifying anything.
- [x] **1.1** Create `.gitignore`, `.env.example` (`MEROSS_EMAIL`, `MEROSS_PASSWORD`, `MEROSS_API_BASE_URL=https://iotx-eu.meross.com`), and `requirements.txt` (`meross-iot`, `python-dotenv`, `fastapi`, `uvicorn`).
- [x] **1.2** Create a standalone diagnostic script `scripts/test_connection.py` that:
  - Authenticates against the EU Meross endpoint.
  - Discovers all online devices and filters/highlights `mts200b` and `mts205b`.
  - Prints basic status (UUID, name, online status, ambient temperature, target temperature).
  - Inspects `device.abilities` to find all namespaces containing `Thermostat` or `Schedule`.
  - Sends a read-only `GET` command via `manager.async_execute_cmd` to the schedule namespace (e.g., `Appliance.Control.Thermostat.Schedule` or `ScheduleB` with payload `{"schedule": [{"channel": 0}]}` or `{"scheduleB": [{"channel": 0}]}`).
  - Saves the complete raw JSON dump to `scripts/schedule_dump.json` and prints it to the console, then cleanly logs out.
- [x] **STOP & VERIFY:** Provide terminal commands to set up `.venv` and run `scripts/test_connection.py`. Wait for the user to confirm the output and inspect `schedule_dump.json`.

---

## Phase 2: Core Meross Service & Schedule Parser (Read/Write Engine)
**Goal:** Build the Python service layer modeled on the exact JSON structure discovered in Phase 1.
- [x] **2.1** Analyze the structure of `scripts/schedule_dump.json` (verify temperature multiplier e.g. `10x` where `205` = `20.5°C`, time representation in minutes from midnight vs slot duration, and number of slots per day).
- [x] **2.2** Create `app/schemas.py` with Pydantic models representing a clean, human-readable schedule (`HH:MM` strings and float temperatures in `°C`) and conversion helpers to/from the raw Meross payload.
- [x] **2.3** Create `app/meross_service.py` encapsulating the singleton `MerossManager`, device caching, `get_devices()`, `get_schedule(uuid)`, and `set_schedule(uuid, schedule_data)`.
- [x] **2.4** Create a safe CLI test or dry-run method to verify reading a schedule, modifying 1 single slot by `0.5°C`, and writing it back to one thermostat.
- [x] **STOP & VERIFY:** Wait for user confirmation that the test modification appears correctly in the official Meross mobile app.

---

## Phase 3: FastAPI Backend & Read-Only Web Dashboard
**Goal:** Expose the service via REST APIs and render the current schedules in the browser.
- [x] **3.1** Create `app/main.py` with FastAPI `lifespan` managing `MerossService` startup/shutdown, plus endpoints:
  - `GET /api/devices`
  - `GET /api/devices/{uuid}/schedule`
- [x] **3.2** Create `static/index.html` (TailwindCSS + Alpine.js) with:
  - Top bar showing both thermostats (`mts200b` and `mts205b`), current ambient temp, target temp, and online status.
  - Weekly read-only grid showing the 7 days (`Mon` to `Sun`) and all time slots formatted as `HH:MM` and `°C`.
  - Collapsible "Raw JSON Inspector" drawer at the bottom for debugging.
- [x] **STOP & VERIFY:** Ask the user to start `uvicorn` and verify that the dashboard loads and displays both thermostats' schedules accurately.

---

## Phase 4: Interactive Editor, Bulk Copy & Sync
**Goal:** Enable full editing and fast replication across days and thermostats.
- [x] **4.1** Add write endpoints in `app/main.py`:
  - `POST /api/devices/{uuid}/schedule` (saves full 7-day schedule to thermostat).
  - `POST /api/devices/{uuid}/clone-to/{target_uuid}` (copies the entire week from one thermostat to the other).
- [x] **4.2** Upgrade `static/index.html` with interactive editing features:
  - Interactive temperature stepping (+/- 0.5°C) and boundary time pickers per slot.
  - Slot addition (split longest slot) and slot deletion (merge with neighbor) while strictly preserving continuous 24-hour coverage.
  - Bulk copy buttons: "Copy to All Days", "Copy to Weekdays (Tue-Fri)", "Copy Saturday to Sunday".
  - Bulk Thermostat Sync: One-click cloning from one thermostat to the other with confirmation.
  - Sticky Unsaved Changes bar with Discard and "Save to Thermostat" action.
- [x] **STOP & VERIFY:** Verify interactive schedule editing, day-to-day bulk copy, and thermostat-to-thermostat sync in the web UI.