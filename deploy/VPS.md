# Deploying on a VPS

A single Linux VPS with Docker replaces the AWS design for a demo. Same image, same Postgres, same
monitoring; what you give up is managed Postgres backups, multi-AZ and autoscaling (see DESIGN.md).

1. Install Docker (`curl -fsSL https://get.docker.com | sh`) and open ports 80 and 443 only.
2. `git clone <repo> && cd candidate_package`
3. Create `.env` next to `docker-compose.vps.yml` (never commit it):
   ```
   POSTGRES_PASSWORD=<long random string>
   API_KEYS=<long random key>
   SITE_ADDRESS=            # or a DNS name pointing at this server, for automatic HTTPS
   ```
4. Create the monitoring secrets:
   ```
   printf '<same key as API_KEYS>' > ops/prometheus/api_key
   cp ops/alertmanager/discord_webhook.example ops/alertmanager/discord_webhook   # then edit in the real URL
   ```
5. `docker compose -f docker-compose.vps.yml up -d --build`
6. Check: `curl http://<server>/healthz`, then call `/score` with `X-API-Key`.
   Prometheus and Alertmanager listen on localhost only; reach them with
   `ssh -L 9090:localhost:9090 -L 9093:localhost:9093 user@server`.

Rollout and rollback are environment variables (`SHADOW_RULESET`, `CANARY_RULESET`,
`CANARY_PERCENT`, `ACTIVE_RULESET`); edit `.env` and run `docker compose -f docker-compose.vps.yml up -d`.

## The console

`http://<server>/` serves the operator console (score form, rollout split, rows read back from Postgres,
Prometheus rules, Alertmanager and a test-alert button, the stack inventory and the demo video). The page is
public but every data call needs the API key. Send the interviewer one link that carries the key in the
fragment, which browsers never send to the server: `http://<server>/#key=<API_KEYS value>`. Rotate the key
afterwards by editing `.env` and `ops/prometheus/api_key`, then `docker compose -f docker-compose.vps.yml up -d`.
Anyone holding the key can write scores to the database, so use a key made for the demo.
