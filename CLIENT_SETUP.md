# ORACLIS Client Setup

Local demo deployment for Windows.

## Requirements

Install:

- Windows 10 or 11, 64-bit
- Python 3.14, 64-bit
- Node.js LTS with npm
- Internet connection during first setup and while using online map/weather features

## Install

1. Copy the complete `ORACLISv3` folder to the client laptop.
2. Do not rename or move files inside the project.
3. Open the project folder.
4. Double-click:

```text
orcalistupi-main\orcalistupi-main\VERIFY_PACKAGE.bat
```

This creates the Python environment, installs backend packages, checks source files, and runs a mock simulation test.

## Start system

Double-click:

```text
RUN_ALL.bat
```

The system opens at:

```text
http://127.0.0.1:5173
```

Local services:

```text
Frontend: http://127.0.0.1:5173
Backend:  http://127.0.0.1:8765
```

Keep both command windows open during demo.

## Stop system

Close both ORACLIS command windows. Or press `Ctrl+C` in each window.

## Verify backend

Open:

```text
http://127.0.0.1:8765/api/health
```

Expected response includes:

```json
{"status":"ok"}
```

## Demo preparation

Run `VERIFY_PACKAGE.bat` before first demo. Run `RUN_ALL.bat` immediately before presentation.

Keep these folders because they contain generated demo data and boundary cache:

```text
orcalistupi-main\orcalistupi-main\outputs\
orcalistupi-main\orcalistupi-main\data\cache\
```

## Troubleshooting

### Browser does not open

Open manually:

```text
http://127.0.0.1:5173
```

### Port already in use

Close old ORACLIS command windows, then run `RUN_ALL.bat` again. The launcher stops processes using ports `8765` and `5173`.

### Package verification fails

Confirm Python 3.14 64-bit is installed. Run `VERIFY_PACKAGE.bat` again while internet is available.

### Map has no tiles

Internet is required for OpenStreetMap tiles. Forecast boundaries and generated data remain local.

### Weather unavailable

Weather uses Open-Meteo. Continue demo with forecast mode if weather provider is unavailable or rate-limited.

### Do not delete

Do not delete:

```text
outputs\
data\cache\
.venv-1\
```

Do not commit or share `.env` files containing secrets.

## Demo limitations

- System runs on the client laptop only.
- Laptop must stay on during demo.
- Local SQLite data is not a backup system.
- Generated outputs can be recreated, but mutable records should be backed up before changes.
- Current forecasts are scenario projections, not official outbreak declarations.

## Quick start

```text
1. VERIFY_PACKAGE.bat
2. RUN_ALL.bat
3. Open http://127.0.0.1:5173
```
