#!/usr/bin/env zsh
#
# Package a skill directory as a zip for upload to claude.ai / the macOS app.
# The archive root is the skill folder itself, which is what the upload expects.

emulate -L zsh
setopt err_exit no_unset pipe_fail

cd "${0:A:h}"

if (( $# != 1 )); then
    print -u2 "usage: ${0:t} SKILL_NAME"
    exit 64
fi

skill=$1

# A bare directory name only -- keeps the output path inside deploy/.
if [[ $skill == */* || $skill == .* ]]; then
    print -u2 "error: SKILL_NAME must be a plain directory name, got '$skill'"
    exit 64
fi

if [[ ! -d $skill ]]; then
    print -u2 "error: no skill directory named '$skill'"
    print -u2 "available skills:"
    for d in *(/N); do
        [[ -f $d/SKILL.md ]] && print -u2 "  $d"
    done
    exit 1
fi

mkdir -p deploy

# zip -r appends to an existing archive, so clear it for a true overwrite.
rm -f deploy/$skill.zip

zip -r deploy/$skill.zip $skill -x '*/__pycache__/*' '*.pyc'

print "\nwrote deploy/$skill.zip"
