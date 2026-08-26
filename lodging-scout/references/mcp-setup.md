# Provider Setup

The skill needs three capabilities. The two MCP ones are satisfied by
whichever server is installed — the skill probes at runtime and does not
depend on any specific one.

| Capability | Served by | Purpose |
|---|---|---|
| `airbnb_search` | MCP | Airbnb listings and details |
| `hotel_search` | MCP | Hotel rates |
| VRBO | Claude in Chrome | VRBO listings and totals |

VRBO is the odd one out. Every VRBO MCP provider is currently blocked by
Akamai, so the skill reads `vrbo.com` in the browser instead — see
[Browser channel](#browser-channel-vrbo). Prefer MCP wherever it works:
one structured call beats a page-load-and-scrape every time, and the two
MCP channels are what let step 3 overlap the slow browser work.

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

### Direct-booking lookup — `@mvanhorn/printing-press-library`

Surfaces host direct-booking sites, which sometimes beat the OTA price.

```bash
npx -y @mvanhorn/printing-press-library install airbnb
```

Two things about this one are easy to get wrong:

- **It is not an MCP server.** It installs a *skill* (`pp-airbnb`) plus a Go
  CLI, so it never appears in `claude mcp list`. It satisfies no capability
  in the table above.
- **The CLI lands in `~/.local/bin`, which is often not on `PATH`.** The
  installed skill invokes `airbnb-pp-cli` by bare name, so without the PATH
  entry it concludes the binary is missing and tries to reinstall it. Fix:

  ```bash
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
  ```

  Claude Code starts each shell from your profile, so a new session picks
  this up once it is in `~/.zshrc`.

Its VRBO support is disabled pending an Akamai workaround, which is why
VRBO moved to the browser.

## Browser channel: VRBO

Requires the Claude in Chrome extension with permission for `vrbo.com`.
Nothing to install from this repo. Because it drives a real logged-in
browser, it sees member pricing an anonymous scraper cannot.

Search URL — build it directly rather than typing into the form:

```
https://www.vrbo.com/search?destination=<url-encoded place>&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&adults=N
```

**Wait for the redirect before reading.** VRBO resolves the destination to
an internal `regionId` and rewrites the URL. Read too early and the
accessibility tree holds only the search form and a "Searching thousands of
properties" spinner — which looks exactly like zero results but is not.
The reliable signal is the tab title: it starts as `www.vrbo.com` and
becomes `... Vacation Rental Search Results` when the results exist. Poll
the title, then read.

Use `find` with a natural-language query rather than dumping the whole
tree; the search page is far larger than the 50k default and the cards are
deeply nested.

Result cards carry both a nightly figure and a stay total, as
`"$2,885 for 3 nights"`, with `"All fees included"` alongside. That total
is an authoritative `provider_total` — pass it straight to
`effective_cost()` and do **not** mark it `~`. Recomposing it from the
nightly rate would be wrong, since the card total already has cleaning and
taxes folded in.

Close the tab when the search is done.

### Hotels

**trivago** — hosted MCP, no API key. This is what `install_to_claude.sh`
sets up, and the only hotel option that also works on claude.ai and mobile,
where local `npx` servers cannot run.

```bash
claude mcp add -s user --transport http trivago https://mcp.trivago.com/mcp
```

Alternatives, all local installs, worth reaching for only if trivago goes
dark. Each satisfies `hotel_search` on its own; there is no benefit to
running several.

- `hotel-goat` — Google Hotels plus Trivago. Installed the printing-press
  way (`npx -y @mvanhorn/printing-press-library install hotel-goat`), so it
  inherits the `~/.local/bin` PATH problem described above, and its Trivago
  data overlaps the hosted server anyway. Its releases also ship an `.mcpb`
  bundle for the Mac app.
- `hotels-skill`, HotelZero — both scrape Booking.com through Playwright: a
  repo, a virtualenv wired up by absolute path, and a Chromium download.

### Scope

`-s user` above puts the server in every project. The skill installs to
`~/.claude/skills`, so it is reachable everywhere; a project-scoped provider
would leave it half-blind in every directory but the one where it was added.

## Preference order

openbnb serves Airbnb; the browser serves VRBO. printing-press is a
direct-booking lookup on top of an Airbnb result, not a search channel.

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
