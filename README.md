# flop-money-path-corpus

Executable golden vectors for the money-path discharge packs posted on the FLOP yellowpaper on
2026-09-28 (yellowpaper #71, #79, #80, #48, #70), against
`flop-labs/yellowpaper@b03378c0` (the 2026-10-13 0.5.0 draft sync; first published against `3c97bbc8`).

Appendix F's corpus (`evidence/wire-format-v1.json`) covers wire encodings only. These packs ask for
balance-level vectors. For each pack this corpus computes the terminal balances under **every
candidate reading** the pack names. It then checks conservation and the stated caps, and asserts the
pack's own numbers. It does **not** choose a reading; the decision belongs to flop-core. Where the 0.5.0 sync ratified
one, the case records it under `ratified` and `--check` asserts that reading's numbers.

```sh
uv run --script money_path.py --check   # JSON current + every pack-stated number reproduced
uv run --script money_path.py --write   # regenerate money-path-v0.json
```

Standard library only. Python ≥ 3.10. Exact arithmetic (`fractions.Fraction`), integer base units.

| Pack | Rule | Readings computed | Pack numbers asserted |
|------|------|-------------------|-----------------------|
| #71 | R10.5 variable settle vs fixed lock | multi-output (A), silent clamp, stranded residual (B), full lock (C, main today per the pack) | L=10000: A=6200 → 6200/3800/0; A>L rejects under A; B strands; clamp underpays |
| #79 | R12.1a gross `payable` vs F.3 audit earmark | no carve, 1% carve with floor, 1% carve with ceil | E=1000 → 1000/0 and 990/10 |
| #80 | R5.4e/R5.4f replacement-provider rent | own counter, own counter clipped to remainder, one counter per slot (continuation), remainder carry. **Ratified: one counter per slot** (D-0524) | c=1,T=2,p/q=1/2 → C_rent=1, literal release 2; ratified A=1, B=0, refund 0 (§5.4) |
| #48 | R12.2 slot release on unilateral close | literal event list, free at `finalize`, free at `force_settle` | open×4 → force_settle → finalize → open fails literally, succeeds at finalize (channels escrow < 50 FLOP each; see #134 for how escrow grants slots) |
| #70 | R5.3d lease top-up vs withdrawal | withdraw wins, extension binds, withdraw ignores pins. **Ratified: withdraw wins** (R5.3f) | transition test register → third-party top-up → withdraw, pinned and unpinned; ratified: pinned rejects, unpinned succeeds |

## What the vectors show beyond the issue threads

- **#79 rounding (pack item 3).** No text names floor or ceil for `payable × audit_fee_split_ppm / 10⁶`.
  They differ by one base unit whenever that product is not a multiple of 10⁶: `payable=1050` gives
  1040/10 under floor and 1039/11 under ceil. `payable=99` gives a zero earmark under floor. The
  same split applies on `force_settle` (F.3 derives `payable` as the tariff `P` there).
  Devnet picks floor, for the 99/1 split it runs today (D-0533). A cooperative `settle` earmarks
  `floor(escrow × audit_fee_split_ppm / 10⁶)` and pays the miner the rest, so `miner_paid + earmark`
  equals the escrow to the base unit. That matches the "1% carve with floor" reading. Measured on
  tzkv-runtime 1014003 at finalized block 2,509,218: all 8 sampled settles with a fractional carve
  match floor, and ceil is one unit off on each
  ([yellowpaper#79](https://github.com/flop-labs/yellowpaper/issues/79#issuecomment-6533543990);
  J0ngAh reports the same for all 238 settled channels on [yellowpaper#224](https://github.com/flop-labs/yellowpaper/issues/224)). The text at 56410c6 still names no
  rule, and ratified D-0525 adds two more products of `payable` (85% and 15%) with no rounding named.
- **#80 (resolved in 0.5.0 by D-0524).** On the pack's counterexample, each rule broke one of the old R5.4f's requirements. The rule
  that pays the replacement (remainder carry) does so by flooring the original below its mandated
  `R_i(t_i)`. The thread already establishes the contradiction (issue body, pepedesigner's
  continuation repair, maho0638's clipping wording). D-0524 matches the continuation (one counter per
  slot), extended to one accumulator per slot and funding tranche. The vectors here are single-tranche.

## Scope

- Illustrative units except where the pack fixes them. #70's deposit and day counts are placeholders.
- #51 (E.48 fee floor) is not included: its pack makes R10.5 amount semantics (#71) a prerequisite.

Contributions to the text are offered under CC BY 4.0, matching the yellowpaper.
