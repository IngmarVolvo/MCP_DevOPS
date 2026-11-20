# SMLCloud DevOps Reader MCP

A deliberately small, read-only MCP server for the Azure DevOps organization `VolvoGroup-UDP` and project `SMLCloud`.

## Tools

- `health`
- `get_work_item`
- `search_work_items`
- `get_work_item_children`
- `list_repositories`
- `get_repository_file`

## Security model for this first working version

The service uses one server-side Azure DevOps PAT. Create it with only these scopes:

- Work Items: Read
- Code: Read

Do not give it write, build execution, release, administration, or packaging scopes. Never put the PAT in Databricks or commit it to Git.

## Run locally

1. Copy `.env.example` to `.env`.
2. Put a read-only PAT in `AZDO_PAT`.
3. Create a virtual environment and install dependencies.
4. Export the variables and start the server.

Linux/macOS example:

    python -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    set -a; . ./.env; set +a
    python server.py

The MCP endpoint is normally:

    http://localhost:8000/mcp

## Run with Docker

    docker build -t smlcloud-devops-mcp .
    docker run --rm -p 8000:8000 --env-file .env smlcloud-devops-mcp

## Deploy

A practical target is Azure Container Apps. Store `AZDO_PAT` as a Container Apps secret and expose port 8000 over HTTPS. Start with ingress restricted to the Databricks egress/network path if possible.

## Connect from Databricks

Register the deployed HTTPS endpoint ending in `/mcp`. This starter does not yet implement end-user OAuth. For an initial controlled proof of concept, protect it using network restrictions or an authenticated reverse proxy. Do not expose it anonymously to the public internet.

## Production hardening

Before broad use:

1. Replace the shared PAT with an Entra-backed identity or an OAuth-on-behalf-of design.
2. Put Azure API Management or an authenticated reverse proxy in front.
3. Add per-user authorization and audit logging.
4. Add response redaction and allowlists for repositories and area paths.
5. Rotate secrets and add health/telemetry alerts.
