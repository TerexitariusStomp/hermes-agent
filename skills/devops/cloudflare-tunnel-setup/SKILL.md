---
name: cloudflare-tunnel-setup
description: Set up Cloudflare named tunnel to expose local services (FastAPI dashboards, APIs) via custom domains with HTTPS
triggers: cloudflare, tunnel, cloudflared, expose, dashboard, public url, https, reverse proxy, templeearth
version: 1.1.0
created: 2026-04-21
category: devops
---

# Cloudflare Named Tunnel Setup

Expose local services (dashboard on :8082, API on :8000, etc.) publicly via a custom domain with automatic HTTPS.

## Key Discovery on This Server (Apr 2026)

- `cloudflared` v2026.3.0 is installed
- Existing Cloudflare credentials in .env do NOT have tunnel management permissions
- All API tokens returned `Authentication error` when used for tunnel creation
- A tunnel token from the Cloudflare dashboard is required

## Method 1: Tunnel Token (Recommended — No CLI Auth Needed)

No origin cert or API token scoping needed. Simplest approach.

### Steps

1. **Get token from Cloudflare dashboard:**
   - Go to Zero Trust > Networks > Tunnels
   - Click Create a tunnel
   - Choose Cloudflared
   - Name it (e.g., `trading`)
   - Copy the token from the install command (starts with `eyJ...`)
   - Add a Public hostname: `trading.templeearth.cc` → Service: `HTTP://localhost:8082`

2. **Install as systemd service:**
   ```bash
   cloudflared service install <TUNNEL_TOKEN>
   systemctl enable cloudflared
   systemctl start cloudflared
   systemctl status cloudflared
   ```

3. **Verify:**
   ```bash
   curl -sI https://trading.templeearth.cc | head -5
   ```

## Method 2: Origin Cert + Config File (More Control)

### Steps

1. **Login to get origin cert:**
   ```bash
   cloudflared tunnel login
   ```
   Opens browser URL → authorizes → saves cert.pem

2. **Create tunnel:**
   ```bash
   cloudflared tunnel create trading
   ```

3. **Create config file:**
   ```yaml
   tunnel: <TUNNEL_ID>
   credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
   ingress:
     - hostname: trading.templeearth.cc
       service: http://localhost:8082
     - service: http_status:404
   ```

4. **Create DNS route:**
   ```bash
   cloudflared tunnel route dns trading trading.templeearth.cc
   ```

5. **Run as service:**
   ```bash
   cloudflared tunnel run --config <config_path> trading
   ```

## API Token Creation (If You Need Tunnel API Access)

To create a token with tunnel management permissions:

1. Go to My Profile > API Tokens > Create Token
2. Permissions needed:
   - Account > Cloudflare Tunnel > Edit
   - Zone > DNS > Edit (for route dns)
3. Account Resources: Include > your account
4. Zone Resources: Include > your domain zone

## Quick Tunnel (Temporary Fallback)

When you can't get named tunnel auth working, use a quick tunnel for immediate access:

```bash
cloudflared tunnel --url http://localhost:8082
```

Gives a random `*.trycloudflare.com` URL. Changes every restart. No auth needed.

**Limitations:**
- URL changes on every restart (not bookmarkable)
- No custom domain
- Cloudflare can revoke at any time
- Not for production use

## Authentication Troubleshooting (Apr 21, 2026)

Tested all existing Cloudflare credentials against the tunnel API:

| Credential | Prefix | Verified | Tunnel API | Notes |
|---|---|---|---|---|
| API Key | `cfut_` | Yes (active) | Auth error | User API Token — limited scopes, no tunnel permission |
| Token 1 | `cfat_` | Invalid | — | Expired or revoked |
| Token 2 | `cfat_` | Invalid | — | Expired or revoked |
| Token 3 | `cfat_` | Invalid | — | Expired or revoked |
| Global Key | `cfk_` | Invalid | — | Not a valid Cloudflare key format |

**Key findings:**
- `cfut_*` prefix = User API Token (not Global API Key). Can verify itself but has narrow permissions
- `cfat_*` prefix = API Tokens (all invalid on this server)
- `cfk_*` is not a standard Cloudflare prefix — Global API Keys are 37-char hex strings
- A valid Bearer token that passes `/user/tokens/verify` can still fail for tunnel endpoints if not scoped
- To check token permissions: `curl -H "Authorization: Bearer TOKEN" https://api.cloudflare.com/client/v4/user/tokens/verify`
- `cloudflared tunnel login` on headless servers requires browser access — run in foreground with long timeout, give user URL

## Method 3: Full API Creation (No Browser Auth Needed)

When you have a valid `cfut_*` User API Token but can't use `cloudflared tunnel login` (headless server). Discovered Apr 21, 2026 during `trading.templeearth.cc` tunnel setup.

### Discovery

- The `.env` had a stale/wrong `CLOUDFLARE_ACCOUNT_ID`
- Using the token against `/accounts` revealed the REAL account ID
- With the correct account ID, full tunnel creation via API worked

### Steps

**1. Find the real account ID:**
```bash
# The token validates but /accounts returns empty if wrong account ID is cached
curl -s "https://api.cloudflare.com/client/v4/accounts" \
  -H "Authorization: Bearer $CF_TOKEN"
# Returns: {"result":[{"id":"REAL_ACCOUNT_ID","name":"..."}]}
```

**2. Create the tunnel via API:**
```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/${REAL_ACCOUNT_ID}/tunnels" \
  -H "Authorization: Bearer ${CF_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"name":"trading","config_src":"cloudflare"}'
```
Returns tunnel ID, credentials, and secret.

**3. Write credentials file:**
```json
// ~/.cloudflared/<TUNNEL_ID>.json
{
  "AccountTag": "REAL_ACCOUNT_ID",
  "TunnelSecret": "<from API response>",
  "TunnelID": "<tunnel_id>"
}
```

**4. Write config file:**
```yaml
# ~/.cloudflared/config.yml
tunnel: <TUNNEL_ID>
credentials-file: /home/terexitarius/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: trading.templeearth.cc
    service: http://localhost:8082
  - service: http_status:404
```

**5. Create DNS CNAME via API:**
```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CF_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"type":"CNAME","name":"trading","content":"<TUNNEL_ID>.cfargotunnel.com","proxied":true}'
```

To find zone ID: `curl ... /zones?name=templeearth.cc`

**6. Run the tunnel:**
```bash
cloudflared tunnel run trading
```

**7. Verify:**
```bash
curl -sI https://trading.templeearth.cc/
curl -s https://trading.templeearth.cc/health
```

### Why This Works

- `cfut_*` User API Tokens authenticate via `Authorization: Bearer` header
- They can verify themselves and access `/accounts` — but the account ID in `.env` may be stale
- Once you have the real account ID, tunnel creation works with the same token
- This avoids `cloudflared tunnel login` entirely (no browser needed)

### Systemd Service (User-Level)
```ini
# ~/.config/systemd/user/cloudflared-trading.service
[Unit]
Description=Cloudflare Tunnel for trading.templeearth.cc
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --config /home/terexitarius/.cloudflared/config.yml run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

## Pitfalls

- Existing tokens may NOT be tunnel-scoped — must create a new token with `Cloudflare Tunnel: Edit` permission
- Dashboard flow is easier than manual API setup for one-off tunnel creation
- `cloudflared tunnel create` needs origin cert — you can't skip `tunnel login` for CLI-based creation
- `cloudflared service install TOKEN` works without origin cert — this is the shortcut
- DNS record: Method 2 requires creating the DNS route separately; Method 1 handles it in the dashboard
- Multiple services: Add multiple hostname entries in ingress config, each with different service port
- localhost binding: Ensure the local service binds correctly (not just localhost if cloudflared runs in a container)
- `cfut_*` tokens are User API Tokens, not Global API Keys — they verify successfully but lack tunnel permissions
- `cloudflared tunnel login` generates a different callback URL each time — don't reuse old URLs
- Background process output may be buffered — use foreground `timeout` or redirect to file to capture quick tunnel URL
- **Stale account ID in .env**: The `CLOUDFLARE_ACCOUNT_ID` may not match the token's actual account. Always verify via `/accounts` endpoint first
- **`cfk_*` is not a valid Cloudflare prefix**: Real Global API Keys are 37-char hex strings, not prefixed
- **`cfat_*` tokens expired**: All three tokens on this server were invalid as of Apr 2026
- Quick tunnel kill vs systemd: Foreground `cloudflared tunnel --url` dies on session end. Use `terminal(background=true)` for session-scoped or systemd for persistence
- **System-level systemd blocked**: Writing to `/etc/systemd/system/` is blocked on this server. Must use user-level systemd at `~/.config/systemd/user/` instead
- **Linger required for user services**: User-level systemd services die on logout unless linger is enabled: `loginctl enable-linger terexitarius`. Verify with `loginctl show-user terexitarius | grep Linger`
- **Config file path in ExecStart**: When using a config file (not tunnel token), the ExecStart must include `--config` flag: `ExecStart=/usr/local/bin/cloudflared tunnel --config /home/terexitarius/.cloudflared/config.yml run`. The simpler `cloudflared tunnel run trading` form works only with token-based installs
- **Service not created by cloudflared**: After tunnel creation via API or dashboard, no systemd unit exists automatically. You must manually create the service file and run `systemctl --user daemon-reload && systemctl --user enable && systemctl --user start`
