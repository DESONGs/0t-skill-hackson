# Claude Code Entry

Canonical repository instructions live in [AGENTS.md](./AGENTS.md).

If you need the shortest operator path, read [START_HERE.md](./START_HERE.md) first.

Use this repository from the root directory and start with:

```bash
./scripts/doctor.sh
cp .env.example .env
# Fill in AVE_API_KEY, API_PLAN, KIMI_API_KEY
./scripts/bootstrap.sh
```

The default `.env.example` keeps the real AVE/Kimi startup path. Use mock mode only for explicit smoke verification.
