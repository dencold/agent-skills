# agent-skills

🤖 Personal [Claude skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview).
Each top-level directory is one skill: a `SKILL.md` plus whatever references,
scripts, and tests it needs.

## Installing a skill

Skills do **not** sync between surfaces. Installing into Claude Code does not
make a skill appear on claude.ai, and an upload to claude.ai is invisible to
Claude Code. Install it wherever you actually want to use it.

### Claude Code

Filesystem-based, no packaging step. Symlink so `git pull` updates the
installed copy:

```bash
git clone https://github.com/dencold/agent-skills.git ~/src/agent-skills
mkdir -p ~/.claude/skills
ln -s ~/src/agent-skills/lodging-scout ~/.claude/skills/lodging-scout
```

- `~/.claude/skills/` — available in every project
- `<project>/.claude/skills/` — that project only, and travels with the repo

Skills are read at session start, so start a new session and ask Claude to
list its skills to confirm.

### Claude desktop app (macOS) and claude.ai

Upload a zip whose **root is the skill folder**, not the files themselves.

1. Settings → Capabilities → enable **Code execution and file creation**.
   Skills do not run without it. On Team/Enterprise an owner enables it
   org-wide first.
2. Package it:

   ```bash
   cd ~/src/agent-skills
   zip -r lodging-scout.zip lodging-scout -x '*/__pycache__/*' '*.pyc'
   ```

3. Customize → Skills → **+** → Create skill → Upload a skill → pick the zip.
4. Toggle the skill on.

Uploaded skills are private to your own account — not shared org-wide, not
centrally managed. There is no pull: re-zip and re-upload to update.

### Claude mobile app (iOS / Android)

Mobile has no zip upload. Skills live on your account, so anything uploaded on
web or in the Mac app shows up in mobile chats, and Customize → Skills toggles
it there.

What a mobile session cannot do: run local MCP servers (anything launched with
`npx`) or read files under `~/.claude/`. Remote MCP connectors do work. For
`lodging-scout` that means mobile has no profile and, unless you have added a
hosted hotel connector, no search providers.

### Trust

A skill is instructions plus code that Claude will execute. Read the SKILL.md
and any bundled scripts before installing one you did not write.

## Skills

### lodging-scout

Searches hotels, Airbnb, and VRBO for a party, dates, and location, then
returns ranked stays with true total-stay cost and honest pros and cons.
Accounts for hotel loyalty status, brand preferences, and family amenities.

**Requires**

- Python 3 for `scripts/lodging_calc.py` (standard library only)
- Node / `npx` for the local MCP providers
- Three MCP capabilities: `airbnb_search`, `vrbo_search`, `hotel_search`. The
  skill probes for these at runtime and refuses to quietly return a
  single-channel search.

**Setup**

1. Install the skill (above). Claude Code is the surface this skill is built
   for — it needs local MCP servers and the profile in `~/.claude/`.
2. Create the profile, which lives outside this repo on purpose (it holds
   loyalty standing and kids' ages):

   ```bash
   cp lodging-scout/references/profile.template.md ~/.claude/lodging-profile.md
   $EDITOR ~/.claude/lodging-profile.md
   ```

3. Install the providers — full table and preference order in
   [`lodging-scout/references/mcp-setup.md`](lodging-scout/references/mcp-setup.md).
   In Claude Code:

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

**Verify**

```bash
python3 -m unittest discover -s lodging-scout/tests   # 46 tests
```

Then walk the six scenarios in
[`lodging-scout/references/verification.md`](lodging-scout/references/verification.md) —
they check the parts the unit tests cannot, like whether the two-room-versus-rental
tradeoff actually gets stated out loud.
