# Vendi

Farmers-market and popup-event management, with independent frontend and backend
project directories.

| Directory | Purpose | Setup |
| --- | --- | --- |
| `frontend/` | Existing Next.js application, including its legacy Auth.js, Prisma, and Stripe backend workflows | [Frontend guide](frontend/README.md) |
| `backend/` | Django API and its separate PostgreSQL database | [Backend guide](backend/README.md) |

## Local development

Run Next.js commands from `frontend/`:

```sh
cd frontend
npm ci
npm run dev
```

The frontend reads `frontend/.env`. Follow its setup guide before first use.
Run Django commands from `backend/`, following the backend guide. Django reads
its own `backend/.env`; its database remains separate from the legacy Prisma
database. This folder move does not migrate business workflows or data.

## Deployment

Before deploying the relocated Next.js app on Vercel, set the project's **Root
Directory** to `frontend`. Keep its existing Next.js build/install defaults and
environment variables. No hosted project settings are changed by this folder move.

Repository-wide engineering instructions remain in [AGENTS.md](AGENTS.md).
