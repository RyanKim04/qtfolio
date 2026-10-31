# TFSA room

The owner tracks TFSA contribution room across institutions. In Settings they enter CRA's confirmed Jan 1 room and the annual limits. On the Dashboard, the `TFSA room` card (`TFSA room · <year>` once configured) walks room forward through hand-recorded contributions, withdrawals, and TFSA→TFSA transfers. Recording first previews warnings (over-contribution, same-year re-contribution) and needs `Record anyway` to proceed past them.

## Sub-features

- `tfsa-unconfigured` makes the card say `Enter CRA's Jan 1 contribution room in Settings to start tracking.` until an anchor exists.
- `tfsa-anchor` takes the Settings → `TFSA contribution room` year plus `Room (CAD)`, then `Save`.
- `tfsa-limits` holds per-year limits (`TBA` = not announced) and an `Add year` button.
- `tfsa-record` uses the card's `Record` tab: date, `Amount (CAD)`, type (`Contribution` / `Withdrawal` / `Transfer (TFSA → TFSA)`), `Institution`, and `Note (optional)`, then `Record`.
- `tfsa-warn` shows a warning for a contribution above the remaining room (text `exceeds your remaining <year> room`), and the buttons become `Record anyway` and `Cancel`. The warning is a plain `div`, not `role=alert`.
- `tfsa-history` uses the `History (N)` tab to list transactions, each with a `×` button (title `Delete`) that asks `confirm()` first.

## How to get to it (user POV)

- Settings → **TFSA contribution room** panel.
- Dashboard → **TFSA room** card → **Record** / **History (N)**.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`. History is optional for TFSA, but the Dashboard is only reachable after an import (onboarding redirect).
- Use synthetic numbers only, such as an anchor of `20000` for the current year. NEVER use the owner's real CRA room.

Steps (`qtv browse tfsa .claude/skills/verify-qtfolio/journeys/tfsa.mjs` runs them all):

- **Unconfigured.** On `/`, the `TFSA room` section shows `Enter CRA's Jan 1 contribution room in Settings`.
- **Anchor.** On `/settings`, in the section headed `TFSA contribution room`, fill `Room (CAD)` with `20000` and click `Save`. Check with `qtv api GET /api/tfsa tfsa-after-anchor`, which shows `configured: true`.
- **Record.** On `/`, click the `Record` tab (the first `Record` in the card), fill `Amount (CAD)` with `1000` (`Institution` already defaults to `Wealthsimple`), and click the submit `Record` (the last one). `Recorded.` appears, room drops by 1,000, and the tab reads `History (1)`.
- **Warning.** Record `50000`. The text `exceeds your remaining` and the button `Record anyway` appear. Click `Cancel`, and check that `History (1)` is unchanged.
- **Delete.** Register `page.once("dialog", (d) => d.accept())`, open `History (1)`, click `getByTitle("Delete")`. The tab reads `History (0)`.
- **Side effect.** Before the delete, `qtv sql "SELECT date, type, amount_cad_cents, institution FROM tfsa_transactions"` returns exactly one contribution of 100000 cents; after it, none.
- **Proof.** Take a `step` per action, with `note()` recording the room before and after.

## Gotchas

- The anchor lives in the scratch DB's settings (`tfsa_anchor`; limits in `tfsa_annual_limits`, and `Save` refuses all-blank limits). Never copy it from the real DB, and never write real figures into evidence you might share.
- Limits for future years may be null (`TBA`). The projection then borrows the last known limit and flags it. That is correct behavior.
- The "re-anchor on CRA's new figure" reminder needs today in March or later and an anchor year before the current year, so a current-year anchor never shows it.
- The Record/History tabs only render once an anchor exists.
- Handles come from `components/TfsaRoom.tsx` and `pages/Settings.tsx`. Settings has a second `Save` (Data sources), so scope to the TFSA panel. Fix this map if a run disagrees.
