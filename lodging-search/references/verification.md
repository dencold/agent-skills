# Verification

Run these by hand after any change to the workflow, rubric, or providers.
Each ends by opening the real booking pages and confirming the skill's
totals reconcile.

## 1. Party of five

"2 adults and 3 kids, ages 4, 6, and 9, San Diego, first week of October,
under $2000."

- [ ] Occupancy check fires **before** the search
- [ ] The two-room-versus-rental tradeoff is stated explicitly, not buried
- [ ] Hotels are priced at 2 rooms
- [ ] Suites or connecting rooms appear as a distinct option

## 2. Loyalty-heavy downtown

A city dense with your status brands, 2 adults, 3 nights.

- [ ] Status brands are boosted, and the boost is visible in the reasoning
- [ ] Perk value is quantified in dollars
- [ ] `total_cost` and `effective_cost_after_perks` both appear, unblended
- [ ] Both reconcile against the booking page

## 3. Beach town, short stay

Somewhere rentals dominate, 2 nights.

- [ ] The cleaning-fee inversion is caught
- [ ] A hotel can beat a lower-nightly-rate rental, and the totals show why
- [ ] Cross-listed Airbnb/VRBO duplicates are merged and the price gap reported

## 4. Pagination, same session

After any search, ask for the next 10.

- [ ] Serves ranks 11–20 with no re-search
- [ ] Repeats nothing from page 1
- [ ] States its position in the pool

## 5. Pagination, later session

Fresh session, ask for more results on a saved trip.

- [ ] Reads the saved pool rather than re-searching
- [ ] Exhausting the pool offers concrete widenings, not a silent stop

## 6. Degraded path

Disable one provider.

- [ ] Failure is loud and names the missing capability
- [ ] Install command is shown
- [ ] Blocked-versus-genuinely-empty are reported differently
- [ ] The user is asked whether to proceed single-channel
