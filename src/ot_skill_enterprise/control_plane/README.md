# Control Plane Skeleton

This package is the Phase A control plane scaffold for `0t-skill_enterprise`.

It is intentionally thin:

- `cli.py` exposes the `0t runtime` namespace.
- `bootstrap.py` collects project, workspace, bridge, and runtime snapshot data.
- `api.py` exposes the runtime, session, and active-run data model for the HTTP/console layer.
- `flows/` owns template definitions and registry helpers.

The goal of this package is to become the coordination layer for agents, providers,
skills, flows, runs, QA, and evolution while staying read-only at the dashboard layer.
