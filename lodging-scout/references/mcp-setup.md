# Provider Setup

The skill needs three capabilities. Each is satisfied by whichever MCP
server is installed — the skill probes at runtime and does not depend on
any specific one.

| Capability | Purpose |
|---|---|
| `airbnb_search` | Airbnb listings and details |
| `vrbo_search` | VRBO listings and details |
| `hotel_search` | Hotel rates |

## Where the config goes

- **Claude Code** — `claude mcp add <name> -- npx -y <package>` for a local
  server, `claude mcp add --transport http <name> <url>` for a hosted one.
  Add `-s user` to make it available in every project instead of just this
  one. The JSON blocks below are the same thing by hand, in `~/.claude.json`
  (personal) or a project `.mcp.json`.
- **Claude for Mac** — Settings → Developer → Edit Config, which opens
  `~/Library/Application Support/Claude/claude_desktop_config.json`. Restart
  the app afterward. Servers shipping an `.mcpb` bundle install by
  double-clicking instead.
- **claude.ai and mobile** — hosted servers only, added under Connectors. A
  local `npx` server cannot run there.

## Known providers, as of 2026-08

### Airbnb — `@openbnb/mcp-server-airbnb`

No API key. Exposes `airbnb_search` and `airbnb_listing_details`.

```json
{
  "mcpServers": {
    "airbnb": {
      "command": "npx",
      "args": ["-y", "@openbnb/mcp-server-airbnb"]
    }
  }
}
```

### Airbnb + VRBO — `@mvanhorn/printing-press-library`

Covers both platforms and surfaces host direct-booking sites, which
sometimes beat the OTA price.

```bash
npx -y @mvanhorn/printing-press-library install airbnb
```

Public search needs no authentication. Its web-search backend is
configurable; DuckDuckGo is the free option.

### Hotels — pick one

- `hotel-goat` — Google Hotels plus Trivago, OTA-aggregated rates, no API key
- `hotels-skill` — Booking.com via Playwright, no API key
- HotelZero — Booking.com via Playwright, 80+ filters
- trivago — hosted MCP at `https://mcp.trivago.com/mcp`, no API key;
  the only hotel option that also works on claude.ai and mobile

## Preference order

When both Airbnb providers are installed, use openbnb for Airbnb (richer
listing detail) and printing-press for VRBO.

## Deduplication

The same house is frequently listed on both Airbnb and VRBO at different
prices. Deduplicate by property address and name. When a duplicate is found
and the prices differ, **report the gap** — it is a finding, not noise, and
it tells the user where to book.

## Health check

Before trusting a provider, run a throwaway search against a busy market on
near dates. Distinguish two failures and report them differently:

- **Blocked or broken** — errored, timed out, or returned malformed data.
  This is a tooling failure. Say so and give the install command.
- **Genuinely empty** — responded correctly with no availability. This is a
  trip-planning fact. Suggest loosening dates or radius.

Never let the second explanation cover for the first.
