# ORACLIS

South Cotabato dengue spatial-intelligence prototype.

## Client handoff

Local Windows demo setup lives in [`CLIENT_SETUP.md`](CLIENT_SETUP.md).

Requirements:

- Windows 10/11 64-bit
- Python 3.14 64-bit
- Node.js LTS with npm
- Internet on first setup and for map/weather features

Quick start:

1. Copy repository to client laptop.
2. Run `orcalistupi-main\orcalistupi-main\VERIFY_PACKAGE.bat`.
3. Run `RUN_ALL.bat`.
4. Open `http://127.0.0.1:5173`.
5. Verify API at `http://127.0.0.1:8765/api/health`.

## Repository layout

- `frontend/`: React, TypeScript, Vite dashboard
- `orcalistupi-main/orcalistupi-main/`: Python simulation engine and local API
- `docs/`: scope, governance, workflow, and technical documentation
- `region12-boundaries-export/`: source boundary files

## GitHub safety

Never commit `.env`, API keys, webhook URLs, virtual environments, `node_modules`, or generated runtime output. Copy `.env.example` to `.env` and add client-owned credentials locally.

Existing credentials must be revoked and replaced before client handoff if they were ever exposed outside trusted local storage.

## Scope limits

ORACLIS produces scenario projections, not official outbreak declarations. Weather is context unless validated by backtesting. Public alerts require LGU or health-office approval.

## Deployment status

Local controlled-demo handoff. Not multi-user production deployment. SQLite remains local and is not a backup system. See [`SUMMARY.md`](SUMMARY.md) for architecture and known limits.
