# lodging-scout

Searches hotels, Airbnb, and VRBO for a party, dates, and location, then
returns ranked stays with true total-stay cost and honest pros and cons.
Accounts for hotel loyalty status, brand preferences, and family amenities.

Note that this skill is best used in Claude Code — it requires local MCP 
servers and the profile in `~/.claude/`. See the note in [the top-level README](../README.md#lodging-scout) 
for more details.

## Requires

- Python 3 for `scripts/lodging_calc.py` (standard library only)
- Node / `npx` for the local MCP providers
- Three MCP capabilities: `airbnb_search`, `vrbo_search`, `hotel_search`. The
  skill probes for these at runtime and refuses to quietly return a
  single-channel search.

## Setup

1. Install the skill. Claude Code is the surface this skill is built
   for — it needs local MCP servers and the profile in `~/.claude/`.
2. Create the profile, which lives outside this repo on purpose (it holds
   loyalty standing and kids' ages):

   ```bash
   cp references/profile.template.md ~/.claude/lodging-profile.md
   $EDITOR ~/.claude/lodging-profile.md
   ```

3. Install the providers — full table and preference order in
   [`references/mcp-setup.md`](references/mcp-setup.md). In Claude Code:

   ```bash
   claude mcp add airbnb -- npx -y @openbnb/mcp-server-airbnb
   npx -y @mvanhorn/printing-press-library install airbnb   # adds VRBO
   ```

   For hotels, pick one from `mcp-setup.md`; `hotel-goat` ships an `.mcpb`
   bundle that installs into the Mac app by double-clicking. trivago's hosted
   server (`https://mcp.trivago.com/mcp`, no key) is the option that also works
   on web and mobile — add it as a Connector there, or in Claude Code:

   ```bash
   claude mcp add --transport http trivago https://mcp.trivago.com/mcp
   ```

4. Nothing to create for output: trip files are written to
   `~/.claude/lodging-trips/` on the first run, and later sessions paginate
   from them.

## Verify

```bash
python3 -m unittest discover -s tests   # 46 tests
```

Then walk the six scenarios in
[`references/verification.md`](references/verification.md) — they check the
parts the unit tests cannot, like whether the two-room-versus-rental tradeoff
actually gets stated out loud.
