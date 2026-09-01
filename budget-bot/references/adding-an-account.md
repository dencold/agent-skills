# Adding an account

The parser stops rather than guessing when a file's header matches no
account. That happens on a genuinely new account, and on a bank that
changed its export format — the second is more common and looks identical.

1. Read the header the error printed.
2. If an existing account's format merely changed, update that block's
   `signature` and any renamed columns. Do not add a second account.
3. Otherwise add a block to `~/.claude/budget-bot/accounts.toml` using
   `accounts.template.toml` as the shape.
4. Determine `amount_sign` by looking at a known purchase in the file. If
   it is negative, use `negative_is_charge`.
5. If the header is identical to an existing account, give both blocks a
   `filename_hint`.
6. Anonymize a few rows into `tests/fixtures/` and add a parse test, so a
   future format change is caught by the suite rather than in production.

Never edit a source CSV to make it parse. The config describes the bank's
format; changing the data hides the problem and breaks the next export.
