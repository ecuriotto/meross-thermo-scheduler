# Project Context & Rules: Meross Thermostat Smart Scheduler

## 1. Goal
Build a local web application in Python to easily view, edit, and bulk-copy the weekly heating schedules of two Meross Wi-Fi thermostats (**models `mts200b` and `mts205b`**), overcoming the tedious day-by-day editing limitation of the official mobile app.

## 2. Tech Stack
- **Python:** Possibly latest stable version inside a local virtual environment (`.venv`).
- **Meross SDK:** `meross-iot` (`>=0.4.7.0`), `python-dotenv`.
- **Backend:** FastAPI + `uvicorn` (strictly `async`).
- **Frontend:** Single Page App served by FastAPI (`static/index.html`) using TailwindCSS (CDN) and Alpine.js. No Node/npm build steps.

## 3. Strict Technical Rules (NEVER VIOLATE)
1. **European Cloud Endpoint:** Always initialize `MerossHttpClient.async_from_user_password` with `api_base_url=os.getenv("MEROSS_API_BASE_URL", "https://iotx-eu.meross.com")` and `creds` cleanly handled.
2. **Connection Lifecycle:** Never re-authenticate on every HTTP request (Meross blocks frequent logins). In FastAPI, manage a single `MerossHttpClient` and `MerossManager` instance inside the `lifespan` context manager, and always call `manager.async_Close()` and `http_client.async_logout()` on shutdown.
3. **Low-Level MQTT Schedule Discovery:** `meross-iot` lacks high-level schedule methods for MTS200 series. Always inspect `device.abilities` first to see which schedule namespace is supported (`Appliance.Control.Thermostat.Schedule` or `Appliance.Control.Thermostat.ScheduleB`), and interact with it via `manager.async_execute_cmd(...)`.
4. **Incremental Execution Rule:** Always follow `@PLAN.md` phase by phase. **STOP at the end of each phase**, ask the user to run/verify the result, and update the checkboxes `[x]` in `PLAN.md` before moving to the next phase.