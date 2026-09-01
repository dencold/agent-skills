#!/usr/bin/env zsh
#
# Install budget-bot into ~/.claude/skills and create its directories.
#
# Safe to re-run: the installed skill directory is replaced wholesale, and
# existing state -- accounts.toml, the merchant map, the emitted ledger --
# is never touched.

emulate -L zsh
setopt err_exit no_unset pipe_fail

src=${0:A:h}
cd $src

if (( $# )); then
    print -u2 "usage: ${0:t}"
    exit 64
fi

claude_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
skill_dir=$claude_dir/skills/budget-bot
state_dir=$claude_dir/budget-bot
drop_dir=$HOME/Documents/budget-bot

# Everything the skill reads at runtime. Tests, the repo README, and build
# leftovers are deliberately absent -- they are not part of the skill.
runtime=(SKILL.md references scripts)

if ! whence -p python3 >/dev/null; then
    print -u2 "error: python3 not found on PATH"
    exit 1
fi

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

for dir in $state_dir $drop_dir; do
    if [[ -d $dir ]]; then
        print "  $dir exists -- left untouched"
    else
        mkdir -p $dir
        print "  $dir created"
    fi
done

if [[ -f $state_dir/accounts.toml ]]; then
    print "  accounts.toml exists -- left untouched"
else
    print "\n  accounts.toml is MISSING. The skill needs it. Start with:"
    print "    cp $src/references/accounts.template.toml $state_dir/accounts.toml"
    print "    \$EDITOR $state_dir/accounts.toml"
fi

print "\nDone. Restart Claude Code to pick up the new skill."
