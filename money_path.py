# /// script
# requires-python = ">=3.10"
# ///
"""Money-path golden vectors for the FLOP yellowpaper v0.5.0 (draft) discharge packs.

Each case computes balances under every candidate reading the pack names and checks
conservation (nothing created, nothing stranded) and the stated caps. It does not pick a
reading: the corpus makes the divergence executable so a decision can be checked against it.

    uv run --script money_path.py            # print the corpus summary
    uv run --script money_path.py --write    # regenerate money-path-v0.json
    uv run --script money_path.py --check    # fail if the JSON is stale or an expectation breaks

Source text: flop-labs/yellowpaper@b03378c0 (yellowpaper.md, 0.5.0 draft sync). Packs: sv's 2026-09-28
discharge comments on yellowpaper #71, #79, #80, #48, #70.
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from math import ceil, floor
from pathlib import Path

SOURCE = "flop-labs/yellowpaper@b03378c0a6d5194d69e5be90968f46e8e6813cb8"
OUT = Path(__file__).with_name("money-path-v0.json")
PPM = 1_000_000
FLOP = 10**18  # base units per FLOP (R12.1d)


class Rejected(Exception):
    """A fail-closed rejection: the extrinsic errors and no balance moves."""


def conserved(before: int, legs: dict[str, int]) -> bool:
    return sum(legs.values()) == before


# --- #71: R10.5 variable settlement against a fixed lock ------------------------------

def r10_5(reading: str, lock: int, attested: int) -> dict[str, int]:
    """Terminal balances for a redeem of an HTLC locked at `lock` with attested amount.

    Legs: redeemer, locker (residual return), escrow (left in the HTLC after SETTLED).
    """
    if reading == "A_multi_output":  # sv preferred: pay A, return L-A, reject A>L
        if attested > lock:
            raise Rejected("AttestedExceedsLock")
        return {"redeemer": attested, "locker": lock - attested, "escrow": 0}
    if reading == "A_clamp":  # sv item 2 names this as the forbidden silent clamp
        paid = min(attested, lock)
        return {"redeemer": paid, "locker": lock - paid, "escrow": 0}
    if reading == "B_stranded":  # pay A, residual stays in a terminal HTLC
        if attested > lock:
            raise Rejected("AttestedExceedsLock")
        return {"redeemer": attested, "locker": 0, "escrow": lock - attested}
    if reading == "C_full_lock":  # flop-core main today per sv: pays full L
        return {"redeemer": lock, "locker": 0, "escrow": 0}
    raise ValueError(reading)


R10_5_READINGS = ["A_multi_output", "A_clamp", "B_stranded", "C_full_lock"]


def case_71() -> dict:
    vectors = []
    for lock, attested in [(10_000, 6_200), (10_000, 10_000), (10_000, 12_000), (10_000, 0)]:
        row = {"inputs": {"L": lock, "A": attested}, "readings": {}}
        for reading in R10_5_READINGS:
            try:
                legs = r10_5(reading, lock, attested)
                row["readings"][reading] = {
                    "legs": legs,
                    "conserves_L": conserved(lock, legs),
                    "terminal_escrow_zero": legs["escrow"] == 0,
                    "redeemer_paid_for_unattested_work": legs["redeemer"] > attested,
                    "redeemer_underpaid_after_work": legs["redeemer"] < attested,
                }
            except Rejected as e:
                row["readings"][reading] = {"rejected": str(e)}
        vectors.append(row)
    return {
        "issue": "yellowpaper#71",
        "rule": "R10.5 + R10.1 conservation",
        "pack_vectors": "A<L, A=L, A>L with L=10000 (sv 2026-09-28); A=0 added as the degenerate attestation",
        "vectors": vectors,
    }


# --- #79: R12.1a gross settle vs F.3 audit earmark ------------------------------------

def audit_earmark(payable: int, ppm: int, rounding: str) -> int:
    exact = Fraction(payable * ppm, PPM)
    return {"floor": floor(exact), "ceil": ceil(exact)}[rounding]


def settle_split(payable: int, ppm: int, rounding: str) -> dict[str, int]:
    earmark = audit_earmark(payable, ppm, rounding)
    return {"miner": payable - earmark, "audit_pool": earmark}


def case_79() -> dict:
    ppm = 10_000  # audit_fee_split_ppm, Appendix A
    vectors = []
    # E=1000 is sv's vector; 1050 and 99 are where floor and ceil part; 1 FLOP is a realistic size.
    for payable, path in [(1000, "settle"), (1050, "settle"), (99, "settle"),
                          (FLOP, "settle"), (FLOP + 50, "settle"), (1050, "force_settle")]:
        row = {"inputs": {"payable": payable, "path": path, "audit_fee_split_ppm": ppm}, "readings": {}}
        row["readings"]["full_escrow_no_carve"] = {"miner": payable, "audit_pool": 0}
        for rounding in ("floor", "ceil"):
            legs = settle_split(payable, ppm, rounding)
            assert conserved(payable, legs)
            row["readings"][f"carve_{rounding}"] = legs
        row["floor_ceil_diverge"] = row["readings"]["carve_floor"] != row["readings"]["carve_ceil"]
        vectors.append(row)
    return {
        "issue": "yellowpaper#79",
        "rule": "R12.1a + F.3 agent receipt row + audit_fee_split_ppm",
        "finding": (
            "F.3's receipt row already answers pack item 2: `payable` is gross for cooperative settle, "
            "the R12.1d tariff P for force_settle, and contract_payment for forward channels, and "
            "'the net transfer to the miner is payable less the audit earmark' for all three. "
            "Item 3 stays open: no text names floor or ceil for payable x ppm / 1e6, and the two "
            "differ by one base unit whenever payable x ppm is not a multiple of 1e6."
        ),
        "vectors": vectors,
    }


# --- #80: R5.4e/R5.4f replacement-provider rent rounding ------------------------------

def R(c: int, t: int, p: int, q: int) -> int:
    """R5.4f cumulative released rent: ceil(c * t * p / q)."""
    return ceil(Fraction(c * t * p, q))


def rent_with_replacement(rule: str, c: int, T: int, p: int, q: int, served_before: int) -> dict:
    """One provider slot, original serves `served_before` blocks, a replacement serves the rest."""
    cap = R(c, T, p, q)  # this slot's term amount inside C_rent
    first = R(c, served_before, p, q)
    rest = T - served_before
    if rule == "own_counter":  # literal R5.4f: replacement settles by its own cumulative counter
        second = R(c, rest, p, q)
    elif rule == "own_counter_capped":  # cap overrides formula: clip to unallocated remainder
        second = min(R(c, rest, p, q), cap - first)
    elif rule == "slot_counter":  # single ceil over the slot: rounding applied once across legs
        second = R(c, T, p, q) - first
    elif rule == "remainder_carry":  # original leg floors its exact share; remainder carries over
        first = floor(Fraction(c * served_before * p, q))
        second = cap - first
    else:
        raise ValueError(rule)
    return {"C_rent_slot": cap, "original": first, "replacement": second,
            "total_released": first + second, "within_cap": first + second <= cap,
            "replacement_paid_for_served_blocks": second >= (R(c, rest, p, q) if rest else 0)}


RENT_RULES = ["own_counter", "own_counter_capped", "slot_counter", "remainder_carry"]


def case_80() -> dict:
    vectors = []
    for c, T, p, q, before in [(1, 2, 1, 2, 1),  # sv's minimal counterexample
                               (3, 10, 1, 4, 3), (1, 100, 1, 3, 50), (7, 9, 2, 5, 4)]:
        row = {"inputs": {"c": c, "T": T, "p": p, "q": q, "original_serves": before}, "readings": {}}
        for rule in RENT_RULES:
            row["readings"][rule] = rent_with_replacement(rule, c, T, p, q, before)
        vectors.append(row)
    return {
        "issue": "yellowpaper#80",
        "rule": "R5.4e C_rent, R5.4f replacement settlement (single funding tranche)",
        "ratified": {"reading": "slot_counter", "by": "D-0524 / R5.4f slot-tranche position accumulator"},
        "vectors": vectors,
    }


# --- #48: R12.2 reservation slot release ----------------------------------------------

BASE_SLOTS = 4  # max_active_reservations_base
SLOT_ESCROW = 50 * FLOP  # escrow_per_reservation_slot


def slots(escrowed: int) -> int:
    return BASE_SLOTS + escrowed // SLOT_ESCROW


def slot_cycle(reading: str) -> dict:
    """Fill every base slot, unilaterally close one, then try to open again with no new escrow."""
    freeing = {
        "literal_R12_2": {"settle", "expire", "timeout", "fraud"},
        "free_at_finalize": {"settle", "expire", "timeout", "fraud", "finalize"},
        "free_at_force_settle": {"settle", "expire", "timeout", "fraud", "force_settle"},
    }[reading]
    active = BASE_SLOTS
    trace = []
    for event in ("force_settle", "finalize"):
        if event in freeing:
            active -= 1
        trace.append({"event": event, "active": active})
    reopen_ok = active < slots(0)
    return {"trace": trace, "reopen_without_extra_escrow": reopen_ok,
            "slots_lost_permanently": 0 if reopen_ok else 1,
            "slot_held_during_challenge_window": "force_settle" not in freeing}


def case_48() -> dict:
    return {
        "issue": "yellowpaper#48",
        "rule": "R12.2 slot release; R12.1d force_settle -> finalize; A7",
        "inputs": {"max_active_reservations_base": BASE_SLOTS, "escrow_granted_slots": 0},
        "assumption": ("each channel escrows less than escrow_per_reservation_slot (50 FLOP), so no "
                       "slot is granted by escrow; under #134's readings A/B a channel of 50 FLOP or "
                       "more funds its own slot and this cycle cannot lose one"),
        "conformance": "open x4 -> force_settle -> finalize -> open must succeed (sv pack)",
        "readings": {r: slot_cycle(r) for r in ("literal_R12_2", "free_at_finalize", "free_at_force_settle")},
    }


# --- #70: R5.3d lease top-up vs publisher withdrawal ----------------------------------

def lease_sequence(reading: str, pinned: bool) -> dict:
    """register(term 180d) -> third party top_up_lease(+30d) -> publisher withdraw_lease."""
    deposit, lease_end = 1_000, 180
    lease_end += 30  # top_up_lease by a third party, free
    if pinned and reading != "withdraw_ignores_pins":
        return {"withdraw": "rejected:Pinned", "deposit_to_publisher": 0, "lease_end": lease_end,
                "storage_duty_continues": True}
    if reading == "withdraw_wins":
        return {"withdraw": "ok", "deposit_to_publisher": deposit, "lease_end": "now",
                "storage_duty_continues": False, "topup_was_cancellable": True}
    if reading == "extension_binds":
        return {"withdraw": "rejected:ExtendedTermBinds", "deposit_to_publisher": 0,
                "lease_end": lease_end, "storage_duty_continues": True,
                "publisher_deposit_locked_by_third_party_days": 30}
    if reading == "withdraw_ignores_pins":
        return {"withdraw": "ok", "deposit_to_publisher": deposit, "lease_end": "now",
                "storage_duty_continues": False, "pinned_consumer_loses_data": pinned}
    raise ValueError(reading)


def case_70() -> dict:
    readings = ("withdraw_wins", "extension_binds", "withdraw_ignores_pins")
    return {
        "issue": "yellowpaper#70",
        "rule": "R5.3d Leased class; P1; pack item 5 transition test",
        "note": "amounts are illustrative units",
        "ratified": {"reading": "withdraw_wins", "by": "R5.3f: publisher-only, rejected while pinned; a top-up never binds"},
        "vectors": [{"pinned": pin, "readings": {r: lease_sequence(r, pin) for r in readings}}
                    for pin in (False, True)],
    }


def corpus() -> dict:
    return {"version": "money-path-v0", "source": SOURCE, "base_unit_per_flop": FLOP,
            "cases": [case_71(), case_79(), case_80(), case_48(), case_70()]}


def expectations(c: dict) -> list[str]:
    """Pack-stated facts every regeneration must still reproduce; returns failures."""
    bad = []
    by = {x["issue"]: x for x in c["cases"]}
    v71 = by["yellowpaper#71"]["vectors"]
    a = v71[0]["readings"]["A_multi_output"]["legs"]
    if a != {"redeemer": 6200, "locker": 3800, "escrow": 0}:
        bad.append("#71 A<L multi-output must be 6200/3800/0")
    if v71[2]["readings"]["A_multi_output"] != {"rejected": "AttestedExceedsLock"}:
        bad.append("#71 A>L must reject under reading A")
    if v71[0]["readings"]["B_stranded"]["terminal_escrow_zero"]:
        bad.append("#71 reading B must strand the residual")
    if not v71[2]["readings"]["A_clamp"]["redeemer_underpaid_after_work"]:
        bad.append("#71 clamp must underpay at A>L")
    v79 = by["yellowpaper#79"]["vectors"][0]["readings"]
    if v79["carve_floor"] != {"miner": 990, "audit_pool": 10} or v79["full_escrow_no_carve"]["miner"] != 1000:
        bad.append("#79 E=1000 must give 1000/0 and 990/10")
    v80 = by["yellowpaper#80"]["vectors"][0]["readings"]
    if v80["own_counter"]["C_rent_slot"] != 1 or v80["own_counter"]["total_released"] != 2:
        bad.append("#80 c=1,T=2,p/q=1/2 must give C_rent=1 and literal release 2")
    s80 = v80["slot_counter"]
    if (s80["original"], s80["replacement"], s80["C_rent_slot"] - s80["total_released"]) != (1, 0, 0):
        bad.append("#80 ratified slot_counter must give A=1, B=0, refund 0 (yellowpaper section 5.4)")
    r48 = by["yellowpaper#48"]["readings"]
    if r48["literal_R12_2"]["reopen_without_extra_escrow"] or not r48["free_at_finalize"]["reopen_without_extra_escrow"]:
        bad.append("#48 literal R12.2 must lose the slot; free-at-finalize must reopen")
    v70 = {x["pinned"]: x["readings"]["withdraw_wins"] for x in by["yellowpaper#70"]["vectors"]}
    if v70[True]["withdraw"] != "rejected:Pinned" or v70[False]["withdraw"] != "ok":
        bad.append("#70 ratified R5.3f must reject withdrawal while pinned and allow it unpinned")
    return bad


def main(argv: list[str]) -> int:
    c = corpus()
    text = json.dumps(c, indent=2, sort_keys=False) + "\n"
    bad = expectations(c)
    for b in bad:
        print("EXPECTATION FAILED:", b, file=sys.stderr)
    if "--write" in argv:
        OUT.write_text(text)
        print(f"wrote {OUT.name} ({len(text)} bytes)")
    elif "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.name} is stale; run --write", file=sys.stderr)
            return 1
        print(f"{OUT.name} up to date; {len(c['cases'])} cases; expectations {'OK' if not bad else 'FAILED'}")
    else:
        print(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
