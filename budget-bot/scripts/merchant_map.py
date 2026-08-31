"""The learned merchant to category map.

This file is the accumulated product of the user's own decisions, so it
is stored as flat sorted CSV -- one line per merchant, hand-editable and
diffable -- rather than anything that needs a tool to inspect.

It lives in ~/.claude/budget-bot/, never in this repo: it is a multi-year
record of where its owner shops.
"""

import csv
import io
from dataclasses import dataclass, field

MAP_COLUMNS = ["merchant", "category", "seen", "ambiguous", "streak", "alternates"]

# Consecutive agreements before the ambiguity flag is offered for removal.
CLEAR_FLAG_STREAK = 5


@dataclass
class MapEntry:
    merchant: str
    category: str
    seen: int
    ambiguous: bool
    streak: int = 0
    alternates: dict[str, int] = field(default_factory=dict)

    def clearable(self):
        """Whether the ambiguity flag has earned an offer to be removed."""
        return self.ambiguous and self.streak >= CLEAR_FLAG_STREAK

    def describe(self):
        """The category as shown in the review table."""
        if not self.ambiguous or not self.alternates:
            return self.category
        runner_up, count = max(self.alternates.items(), key=lambda kv: kv[1])
        return f"{self.category} (usually; {count} of {self.seen} were {runner_up})"


def _pack(alternates):
    """Encode alternates dict as CSV row for safe punctuation handling.

    Flattens the dict to a single CSV row: [category1, count1, category2, count2, ...]
    The csv module's quoting handles all punctuation correctly.
    """
    if not alternates:
        return ""

    output = io.StringIO()
    writer = csv.writer(output)
    row = []
    for k, v in sorted(alternates.items()):
        row.append(k)
        row.append(str(v))
    writer.writerow(row)
    return output.getvalue().rstrip('\r\n')


def _unpack(text):
    """Decode alternates from CSV format.

    Reads the CSV row and pairs up consecutive elements back to dict.
    Raises ValueError if the format is malformed.
    """
    if not text or not text.strip():
        return {}

    output = {}
    input_stream = io.StringIO(text)
    reader = csv.reader(input_stream)
    try:
        row = next(reader)
    except StopIteration:
        return {}

    # Pair up the values: row[0]=category, row[1]=count, row[2]=category, row[3]=count, ...
    if len(row) % 2 != 0:
        raise ValueError(f"Malformed alternates data: odd number of fields in '{text}'")

    for i in range(0, len(row), 2):
        category = row[i]
        try:
            count = int(row[i + 1])
        except ValueError:
            raise ValueError(f"Invalid count for category '{category}': '{row[i + 1]}'")
        output[category] = count

    return output


def load_map(path):
    """Read the map. A missing file is an empty map, not an error."""
    try:
        handle = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}

    with handle:
        entries = {}
        for record in csv.DictReader(handle):
            merchant = (record["merchant"] or "").strip()
            if not merchant:
                continue
            try:
                alternates = _unpack(record.get("alternates"))
            except ValueError as e:
                raise ValueError(f"Error loading merchant '{merchant}': {e}") from e
            entries[merchant] = MapEntry(
                merchant=merchant,
                category=(record["category"] or "").strip(),
                seen=int(record.get("seen") or 0),
                ambiguous=(record.get("ambiguous") or "").strip().lower() == "true",
                streak=int(record.get("streak") or 0),
                alternates=alternates,
            )
    return entries


def save_map(path, entries):
    """Write the map sorted by merchant so diffs stay readable."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MAP_COLUMNS)
        for merchant in sorted(entries):
            entry = entries[merchant]
            writer.writerow([
                entry.merchant, entry.category, entry.seen,
                "true" if entry.ambiguous else "false",
                entry.streak, _pack(entry.alternates),
            ])


def record_decision(entries, merchant, category):
    """Fold one confirmed categorization back into the map.

    A disagreement with the stored category is sufficient evidence that a
    merchant genuinely splits -- Target is Household most trips and Gifts
    some -- so it sets the flag on first occurrence. The majority category
    stays the default; the alternate is counted so the review line can show
    the split.
    """
    entry = entries.get(merchant)
    if entry is None:
        entry = MapEntry(merchant=merchant, category=category, seen=1, ambiguous=False)
        entries[merchant] = entry
        return entry

    entry.seen += 1
    if category == entry.category:
        entry.streak += 1
    else:
        entry.alternates[category] = entry.alternates.get(category, 0) + 1
        entry.ambiguous = True
        entry.streak = 0
    return entry
