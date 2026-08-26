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
2. Package it with `package-skill.sh`, which takes the skill's directory
   name and writes `deploy/<skill>.zip`:

   ```bash
   cd ~/src/agent-skills
   ./package-skill.sh lodging-scout
   ```

   It builds the archive with the skill folder as the root, drops
   `__pycache__/` and `*.pyc`, creates `deploy/` on first run, and fully
   replaces an existing zip rather than adding to it. Run it with no
   arguments, or with a name that is not a skill, and it lists what is
   available. `deploy/` is gitignored.

3. Customize → Skills → **+** → Create skill → Upload a skill → pick the zip.
4. Toggle the skill on.

Uploaded skills are private to your own account — not shared org-wide, not
centrally managed. There is no pull: re-run the script and re-upload to
update.

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

### [lodging-scout](lodging-scout/)

Searches hotels, Airbnb, and VRBO for a party, dates, and location, then
returns ranked stays with true total-stay cost and honest pros and cons.
Accounts for hotel loyalty status, brand preferences, and family amenities.

Needs Python 3, `npx`, and three MCP providers — setup and verification steps
are in the [skill's README](lodging-scout/README.md).
