"""Optional, local-only acquisition evidence; never participates in pricing.

Enabled only by an attempt UUID and an existing absolute attempt directory.
Every observer catches its own errors. No HTTP, database calls, or retries occur
here. Files are published once by atomic link; an existing artifact is never
replaced. The org-side recorder uploads and retries these files separately.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
import unicodedata
import uuid

_ACTIVE = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _warn(exc):
    # Do not print arbitrary exception values (which may contain credentials).
    try:
        import sys
        print(f"[collection-shadow] local evidence unavailable: {type(exc).__name__}",
              file=sys.stderr)
    except Exception:
        pass


def _safe(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except Exception as exc:
        _warn(exc)
        return None


def observe(fetcher, method, *args):
    def capture():
        recorder = getattr(fetcher, "_collection_evidence", None)
        if recorder is not None:
            getattr(recorder, method)(*args)
    _safe(capture)


def observe_mapping_rows(game, rows):
    def capture():
        _ACTIVE.mapping_rows.extend(json.loads(json.dumps(rows)))
    if _ACTIVE is not None and _ACTIVE.game == game:
        _safe(capture)


def observe_mapping_source(game, source):
    def capture():
        _ACTIVE.mapping_inputs_status = source
        _ACTIVE.mapping_observed_at = _now()
    if _ACTIVE is not None and _ACTIVE.game == game:
        _safe(capture)


def _atomic_once(path, payload):
    payload = dict(payload)
    payload["artifact_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    raw = _canonical(payload)
    fd, tmp = tempfile.mkstemp(prefix=".collection-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
        os.link(tmp, path)  # O_EXCL semantics; readers never see a partial file.
    finally:
        os.unlink(tmp)


def _version():
    root = Path(__file__).resolve().parents[1]
    paths = ("main.py", "src/config.py", "src/homura_fetcher.py", "src/collection_evidence.py",
             "src/ledger_master.py", "src/price_comparator.py", "src/supabase_writer.py")
    digest = hashlib.sha256()
    for name in paths:
        digest.update(name.encode("utf-8") + b"\0" + (root / name).read_bytes())
    dirty = os.getenv("COLLECTION_COLLECTOR_DIRTY")
    return {"commit": os.getenv("COLLECTION_COLLECTOR_COMMIT"),
            "dirty": {"0": False, "1": True}.get(dirty),
            "code_sha256": digest.hexdigest()}


def observed_fetch(function):
    """Record completion/failure while preserving the wrapped result/exception."""
    @functools.wraps(function)
    def wrapped(fetcher, game, *args, **kwargs):
        global _ACTIVE
        recorder = _safe(CollectionEvidence.from_environment, game.value)
        fetcher._collection_evidence = recorder
        _ACTIVE = recorder
        try:
            result = function(fetcher, game, *args, **kwargs)
        except BaseException as exc:
            if recorder is not None:
                _safe(recorder.finish, False, type(exc).__name__)
            raise
        if recorder is not None:
            _safe(recorder.finish, True, None)
        return result
    return wrapped


class CollectionEvidence:
    def __init__(self, attempt_id, directory, game):
        self.attempt_id = attempt_id
        self.directory = directory
        self.game = game
        self.started_at = _now()
        self.categories = []
        self.candidates = []
        self.mapping_rows = []
        self.mapping_inputs_status = "not_observed"
        self.mapping_observed_at = None
        self.category = None
        self.page = None
        self.stage = "fetch"
        self.version = _version()

    @classmethod
    def from_environment(cls, game):
        attempt_id = os.getenv("COLLECTION_ATTEMPT_ID")
        directory = os.getenv("COLLECTION_ATTEMPT_DIR")
        if not attempt_id and not directory:
            return None
        if not attempt_id or not directory:
            raise ValueError("incomplete attempt configuration")
        attempt_id = str(uuid.UUID(attempt_id))
        path = Path(directory)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("attempt directory must already exist")
        if game not in {"pokemon", "onepiece", "dragonball", "yugioh"}:
            raise ValueError("unsupported game")
        return cls(attempt_id, path, game)

    def category_started(self, category_id, metadata):
        self.stage = "fetch"
        self.category = {
            "category_id": category_id, "game": metadata.get("game"),
            "external_unit": metadata.get("unit"), "started_at": _now(),
            "finished_at": None, "complete": False,
            "reason_code": "category_started", "pages": [],
        }
        self.categories.append(self.category)

    def page_received(self, url, html):
        self.stage = "parse"
        self.page = {"url": url, "observed_at": _now(),
                     "response_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                     "card_count": 0, "parsed_count": 0, "terminal": False,
                     "parse_complete": False}
        self.category["pages"].append(self.page)

    def card_count(self, count):
        self.page["card_count"] = count

    def candidate(self, raw_name, name, raw_code, href, price, price_text):
        # The historical parser is intentionally unchanged. Validate its value
        # independently, without silently accepting prefixes such as "¥12abc".
        normalized = unicodedata.normalize("NFKC", price_text).strip()
        valid_text = bool(re.fullmatch(
            r"¥\s*(?:[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\s*円)?", normalized))
        valid = type(price) is int and price > 0 and valid_text and bool(name)
        self.page["parsed_count"] += int(valid)
        reason = "valid_price" if valid else "invalid_or_missing_price"
        if self.category["game"] != self.game:
            valid, reason = False, "category_game_mismatch"
        self.candidates.append({
            "external_ref": raw_code, "raw_external_ref": raw_code,
            "raw_name": raw_name, "name": name,
            "external_unit": self.category["external_unit"],
            "category_id": self.category["category_id"],
            "observed_price": price, "raw_price": price_text,
            "observed_at": self.page["observed_at"],
            "url": href, "page_url": self.page["url"],
            "parse_ok": valid, "reason_code": reason,
        })

    def page_finished(self, terminal):
        self.page["terminal"] = terminal
        self.page["parse_complete"] = (
            self.page["card_count"] > 0
            and self.page["parsed_count"] == self.page["card_count"]
        )
        self.stage = "fetch"

    def category_finished(self):
        pages = self.category["pages"]
        complete = bool(pages) and pages[-1]["terminal"] and all(
            page["parse_complete"] for page in pages)
        self.category.update(
            complete=complete, finished_at=_now(),
            reason_code="complete_category" if complete else "unverified_page_structure",
        )
        self.category = None

    def resolved_candidates(self, resolver, master, normalize, extract_inline):
        # Use the same already-loaded mapping as _merge, retaining all original
        # rows. No extra ledger request and no dict overwrite of duplicate refs.
        names = {}
        for code, name in master.items():
            names.setdefault(normalize(name), set()).add(code)
        for row in self.candidates:
            code, _ = resolver(row["name"], row["raw_external_ref"])
            row["external_ref"] = code
            inline, _ = extract_inline(row["name"])
            name_matches = names.get(normalize(row["name"]), set())
            explicit_ref = bool(inline) or (code == row["raw_external_ref"] and code in master)
            row["identity_resolved"] = bool(code) and (
                explicit_ref or (len(name_matches) == 1 and code in name_matches))
            if len(name_matches) > 1 and not explicit_ref:
                row["reason_code"] = "ambiguous_master_name"
        # A new/unresolved alias may be a renamed expected item. Such a category
        # cannot prove absence, even though all numeric fields parsed normally.
        unresolved = {r["category_id"] for r in self.candidates if not r["identity_resolved"]}
        for category in self.categories:
            if category["category_id"] in unresolved and category["complete"]:
                category.update(complete=False, reason_code="unresolved_candidate")

    def envelope(self):
        return {"schema_version": 2, "attempt_id": self.attempt_id,
                "source": "homura", "game": self.game,
                "generated_at": _now(), "collector_version": self.version,
                "observed_at": next((p["observed_at"] for c in self.categories
                                     for p in c["pages"]), None)}

    def finish(self, succeeded, error_type):
        if self.category is not None:
            self.category.update(
                finished_at=_now(), complete=False,
                reason_code="parse_exception" if self.stage == "parse" else "fetch_exception",
            )
        # Exact responsive duplicates are one observation. Distinct URLs or
        # prices remain distinct candidates, even when _merge collapses them.
        unique = {}
        for row in self.candidates:
            key = _canonical(row)
            unique.setdefault(key, row)
        payload = self.envelope()
        payload.update(fetch_succeeded=succeeded, error_type=error_type,
                       started_at=self.started_at, categories=self.categories,
                       candidates=list(unique.values()), mapping_inputs=self.mapping_rows,
                       mapping_inputs_status=self.mapping_inputs_status,
                       mapping_observed_at=self.mapping_observed_at)
        _atomic_once(self.directory / f"homura-{self.game}.json", payload)


def observe_comparison(game, results, decrease_holds, increase_holds,
                       approve_decreases, approve_increases):
    def write():
        rows = []
        holds = {(h.code, h.unit) for h in
                 ([] if approve_decreases else decrease_holds)
                 + ([] if approve_increases else increase_holds)}
        for result in results:
            for suffix, unit in (("1", "BOX"), ("2", "NS" if game == "pokemon" else "CARTON")):
                price = getattr(result, "competitor_price_" + suffix)
                if price is None:
                    continue
                current = getattr(result, "current_price_" + suffix)
                proposed = getattr(result, "recommended_price_" + suffix)
                reason = ("guard_hold" if (result.code, unit) in holds else
                          "same_value" if proposed == current and proposed is not None else
                          "comparison_only")
                rows.append({"external_ref": result.code, "external_unit": unit,
                             "reason_code": reason, "current_price": current,
                             "proposed_price": proposed})
        payload = _ACTIVE.envelope()
        payload.update(stage="comparison", decisions=rows)
        _atomic_once(_ACTIVE.directory / f"homura-{game}.comparison.json", payload)
    if _ACTIVE is not None and _ACTIVE.game == game:
        _safe(write)


def observe_payloads(game, payloads, violations, batch_stopped=False):
    def write():
        payload = _ACTIVE.envelope()
        decisions = [
            {"external_ref": v.code, "external_unit": unit,
             "reason_code": "guard_hold", "guard": "price_guard"}
            for v in violations for unit in ("BOX", "NS" if game == "pokemon" else "CARTON")]
        if batch_stopped:
            decisions = [
                {"external_ref": item.code, "external_unit": unit,
                 "reason_code": "guard_hold", "guard": "price_guard_batch"}
                for item in payloads
                for suffix, unit in (("1", "BOX"), ("2", "NS" if game == "pokemon" else "CARTON"))
                if getattr(item, "new_price_" + suffix) is not None]
        payload.update(stage="payloads", batch_stopped=batch_stopped, decisions=decisions)
        _atomic_once(_ACTIVE.directory / f"homura-{game}.payloads.json", payload)
    if _ACTIVE is not None and _ACTIVE.game == game:
        _safe(write)


def observe_manual_holds(game, held):
    def write():
        payload = _ACTIVE.envelope()
        payload.update(stage="writer", decisions=[
            {"product_id": h["product_id"], "canonical_unit": h["unit"],
             "reason_code": "guard_hold", "guard": "same_day_manual",
             "current_price": h["manual"], "proposed_price": h["calc"]} for h in held])
        _atomic_once(_ACTIVE.directory / f"homura-{game}.writer.json", payload)
    if _ACTIVE is not None and _ACTIVE.game == game:
        _safe(write)
