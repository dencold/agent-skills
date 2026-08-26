#!/usr/bin/env zsh
#
# Install lodging-scout into ~/.claude/skills and set up its MCP providers.
#
# Safe to re-run: the installed skill directory is replaced wholesale, MCP
# servers already configured are left alone, and the profile is never written.

emulate -L zsh
setopt err_exit no_unset pipe_fail

src=${0:A:h}
cd $src

if (( $# )); then
    print -u2 "usage: ${0:t}"
    exit 64
fi

claude_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
skill_dir=$claude_dir/skills/lodging-scout
profile=$claude_dir/lodging-profile.md

# Everything the skill reads at runtime. Tests, the repo README, and build
# leftovers are deliberately absent -- they are not part of the skill.
runtime=(SKILL.md references scripts)

typeset -a failed

need() {
    if ! whence -p $1 >/dev/null; then
        print -u2 "error: '$1' not found on PATH -- $2"
        exit 1
    fi
}

# Servers are added at user scope: the skill installs to $claude_dir and is
# therefore available in every project, so project-scoped providers would leave
# it half-blind everywhere but the directory this script ran in.
add_server() {
    local name=$1
    shift

    if claude mcp get $name >/dev/null 2>&1; then
        print "  $name: already configured, leaving it alone"
        return 0
    fi

    if "$@" >/dev/null; then
        print "  $name: added (user scope)"
    else
        print -u2 "  $name: FAILED -- run by hand: $*"
        failed+=($name)
    fi
}

# printing-press packages register themselves rather than being added by name,
# so there is nothing to probe first -- their installer is the idempotency check.
vendor_install() {
    local label=$1 package=$2

    print "  $label: running printing-press installer"
    if ! npx -y @mvanhorn/printing-press-library install $package; then
        print -u2 "  $label: FAILED -- run by hand: npx -y @mvanhorn/printing-press-library install $package"
        failed+=($label)
    fi
}

need claude "install Claude Code first"
need npx "the Airbnb provider runs under Node"
need python3 "scripts/lodging_calc.py needs Python 3"

for item in $runtime; do
    if [[ ! -e $item ]]; then
        print -u2 "error: missing '$item' -- run this script from a full checkout"
        exit 1
    fi
done

print "Installing skill to $skill_dir"
rm -rf $skill_dir
mkdir -p $skill_dir
cp -R $runtime $skill_dir/
# cp -R brings along whatever Python left behind in scripts/.
find $skill_dir \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
print "  $(find $skill_dir -type f | wc -l | tr -d ' ') files"

print "\nInstalling stay providers"
add_server airbnb claude mcp add -s user airbnb -- npx -y @openbnb/mcp-server-airbnb
vendor_install "printing-press (direct-booking lookup)" airbnb

# VRBO has no working MCP provider -- printing-press disabled it pending an
# Akamai workaround -- so the skill reads vrbo.com through Claude in Chrome.
# Nothing to install; the extension is configured in Chrome, not here.
print "  vrbo: served by Claude in Chrome, nothing to install"

# trivago is hosted, so it satisfies hotel_search with no local install and no
# PATH to get wrong. mcp-setup.md lists the local alternatives; none of them
# earn the extra moving parts while this one is healthy.
print "\nInstalling hotel provider"
add_server trivago claude mcp add -s user --transport http trivago https://mcp.trivago.com/mcp

print "\nProfile"
if [[ -f $profile ]]; then
    print "  $profile exists -- left untouched"
else
    print "  $profile is MISSING. The skill needs it. Create it with:"
    print "    cp $src/references/profile.template.md $profile"
    print "    \$EDITOR $profile"
fi

if (( $#failed )); then
    print -u2 "\nDone, but these providers need attention: $failed"
    exit 1
fi

print "\nDone. Restart Claude Code to pick up the new skill and servers."
