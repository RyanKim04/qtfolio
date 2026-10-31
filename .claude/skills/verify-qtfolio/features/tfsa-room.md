# TFSA room

The owner tracks TFSA contribution room across institutions. In Settings they enter CRA's confirmed Jan 1 room and the annual limits. On the Dashboard, the `TFSA room · <year>` card walks room forward through hand-recorded contributions, withdrawals, and TFSA→TFSA transfers. Recording first previews warnings (over-contribution, same-year re-contribution) and needs `Record anyway` to proceed past them.

## Sub-features

- `tfsa-unconfigured` makes the card say `Enter CRA's Jan 1 contribution room in Settings to start tracking.` until an anchor exists.
- `tfsa-anchor` takes the Settings → `TFSA contribution room` year plus `Room (CAD)`, then `Save`.
- `tfsa-limits` holds per-year limits (`TBA` = not announced) and an `Add year` button.
- `tfsa-record` uses the card's `Record` tab: date, `Amount (CAD)`, type (`Contribution` / `Withdrawal` / `Transfer (TFSA → TFSA)`), `Institution`, and `Note (optional)`, then `Record`.
- `tfsa-warn` shows a warning for a contribution above the remaining room, and the button becomes `Record anyway`.
- `tfsa-history` uses the `History (N)` tab to list transactions, each with a `Delete` control.

## How to get to it (user POV)

- Settings → **TFSA contribution room** panel.
- Dashboard → **TFSA room** card → **Record** / **History (N)**.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`. History is optional for TFSA, but the Dashboard is only reachable after an import (onboarding redirect).
- Use synthetic numbers only, such as an anchor of `20000` for the current year. NEVER use the owner's real CRA room.

Steps:

- **Unconfigured.** On `/`, the `TFSA room` section shows `Enter CRA's Jan 1 contribution room in Settings`.
- **Anchor.** On `/settings`, in the section headed `TFSA contribution room`, fill `Room (CAD)` with `20000` and click `Save`. Check with `qtv api GET /api/tfsa tfsa-after-anchor`, which shows `configured: true`.
- **Record.** On `/`, click `Record` in the TFSA card, fill `Amount (CAD)` with `1000`, set the `Institution` to `Wealthsimple`, and click `Record`. Room drops by 1,000. `History (1)` lists it.
- **Warning.** Record `50000`. A warning alert appears and the button reads `Record anyway`. Do not confirm, and check that `History (1)` is unchanged.
- **Side effect.** Run `qtv sql "SELECT date, type, amount_cad_cents, institution FROM tfsa_transactions"`, which returns exactly one contribution of 100000 cents.
- **Proof.** Take a `step` per action, with `note()` recording the room before and after.

## Gotchas

- The anchor lives in the scratch DB's settings (`tfsa_anchor`). Never copy it from the real DB, and never write real figures into evidence you might share.
- Limits for future years may be null (`TBA`). The projection then borrows the last known limit and flags it. That is correct behavior.
- The March+ "re-anchor on CRA's new figure" reminder depends on today's date.
- This file has not been driven end to end yet. Handles come from `components/TfsaRoom.tsx` and `pages/Settings.tsx`. Fix this map if a run disagrees.
