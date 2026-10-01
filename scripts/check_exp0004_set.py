"""
Structure checker for the frozen EXP-0004 set (tests/regression/exp0004/{dev,test}.json + MANIFEST.json).

Prints ONLY counts, constraint results and sha256s. It never prints task, history, answer or regex text, so it is safe
to run on the sealed test split. Exit status 0 when every constraint holds, 1 otherwise.

Verifies every constraint of the EXP-0004 builder brief (commit 21c5706): split sizes and sets; 60-150 episodes per
scope in chronological days of exactly 10; D-0018 event shapes, D-0012 trust derivation and backward-only refs;
>= 40 distinct lesson-bearing (authoritative) corrections per scope, also after erasure; exactly 4 tasks per scope
and the fixed type mix; the 30 erasure targets (15 T1 + 15 T2, 5 + 5 per set) with person-authored targets, listed
erase persons, expected_ask and "only source" checks; T2 token Jaccard <= 0.20; T3 v1 / v2 ordering with >= 2
independent counter-episodes; T5 unanswerable with near distractors; >= 5 high-overlap distractors per target fact;
cross-scope twins without a grant; injections in non-authoritative sections; regex compilation and non-matching
rules; no names reused from other regression sets; dev/test disjointness; a secret-shape scan; MANIFEST hashes.

Usage: python3 scripts/check_exp0004_set.py
"""
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "regression" / "exp0004"
_spec = importlib.util.spec_from_file_location("make_exp0004_set", ROOT / "scripts" / "make_exp0004_set.py")
GEN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GEN)
tokens, STOP_WORDS = GEN.tokens, GEN.STOP_WORDS

EVENT_TYPES = {"decision", "prediction", "action", "outcome"}
ROLES = {"status", "evaluation", "correction", "diagnostic", "operator_note"}
RELS = {"outcome_for", "execution_of", "response_to", "correction_of", "evaluates_prediction", "continuation_of"}
REGEX_FIELDS = ("answer_regex", "stale_regex", "injection_regex", "cross_scope_regex", "erased_regex")
FAILS = Counter()
STATS = defaultdict(list)


def fail(name, cond):
    if not cond:
        FAILS[name] += 1
    return cond


def jac(a, b):
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def trust_of(source, authorship):  # D-0012 part A, re-implemented independently of the generator
    if source in ("web", "tool") or authorship == "external":
        return "untrusted"
    if authorship == "integration_result":
        return "trusted" if source in ("git", "ci", "review", "system") else "invalid"
    if authorship == "scope_principal":
        return "trusted" if source in ("chat", "git", "review", "system") else "untrusted"
    return "invalid"


def is_auth(ev, sec):  # D-0018 authority rule
    if not (sec["role"] == "correction" or (sec["role"] == "evaluation" and ev["body"].get("success") is False)):
        return False
    return ev["trust"] == "trusted" and (ev["source"] in ("ci", "review", "git") or ev["actor_kind"] == "person")


def ev_texts(ev):
    b = ev["body"]
    out = [b.get(k) for k in ("decision_text", "expected_outcome", "expected_failing_check", "description")]
    out += [s["text"] for s in b.get("sections", [])] + list(b.get("failing_checks", []))
    return [t for t in out if isinstance(t, str)]


def corr_text(ev):
    return " ".join(s["text"] for s in ev["body"].get("sections", []) if s["role"] == "correction")


def check_event_shape(ev, seen_ids):
    b = ev["body"]
    et = ev["event_type"]
    fail("event_type", et in EVENT_TYPES)
    fail("actor_kind", ev["actor_kind"] in ("agent", "person", "system"))
    fail("trust_derivation", trust_of(ev["source"], ev["authorship"]) == ev["trust"])
    for r in ev["refs"]:
        fail("ref_rel", r["rel"] in RELS)
        fail("ref_backward_same_scope", r["event_id"] in seen_ids)
    if et == "decision":
        fail("decision_body", isinstance(b.get("decision_text"), str) and b.get("reasoning_owner") == "external"
             and isinstance(b.get("decision_kind"), str))
    elif et == "prediction":
        fail("prediction_body", isinstance(b.get("expected_outcome"), str) and b.get("predictor") in ("agent", "predictor")
             and any(r["rel"] == "response_to" for r in ev["refs"]))
    elif et == "action":
        fail("action_body", b.get("dispatched") is True and isinstance(b.get("action_kind"), str)
             and any(r["rel"] == "execution_of" for r in ev["refs"]))
    else:
        fail("outcome_body", b.get("success") in (True, False, None) and len(b.get("sections", [])) >= 1
             and all(s["role"] in ROLES and s["text"].strip() for s in b["sections"])
             and any(r["rel"] == "outcome_for" for r in ev["refs"]))
        fc = b.get("failing_checks", [])
        fail("failing_checks", len(fc) == len({c.strip().casefold() for c in fc}) <= 50 and all(c.strip() for c in fc))


def compile_all(task):
    out = {}
    for f in REGEX_FIELDS:
        v = task.get(f)
        if v is None:
            continue
        try:
            out[f] = re.compile(v)
        except re.error:
            fail("regex_compiles", False)
            continue
        fail("regex_not_in_prompt", not out[f].search(task["prompt"]))
        fail("regex_not_in_addresses", not any(out[f].search(a) for a in task["addresses"]))
    return out


def check_scope(sc, by_id, split):
    sid = sc["scope_id"]
    nd = len(sc["days"])
    fail("days_consecutive", [d["day"] for d in sc["days"]] == list(range(1, nd + 1)))
    fail("days_dates_increasing", all(a["date"] < b["date"] for a, b in zip(sc["days"], sc["days"][1:])))
    fail("days_of_10", all(len(d["episodes"]) == 10 for d in sc["days"]))
    n_ep = sum(len(d["episodes"]) for d in sc["days"])
    STATS[f"{split}_episodes"].append(n_ep)
    fail("episodes_60_150", 60 <= n_ep <= 150)
    events, day_of, ep_of, seen = {}, {}, {}, set()
    for d in sc["days"]:
        for ep in d["episodes"]:
            for ev in ep["events"]:
                check_event_shape(ev, seen)
                fail("event_id_unique", ev["event_id"] not in seen)
                seen.add(ev["event_id"])
                events[ev["event_id"]] = ev
                day_of[ev["event_id"]] = d["day"]
                ep_of[ev["event_id"]] = ep["episode_id"]
    STATS[f"{split}_events"].append(len(events))
    erased = set(sc["erase_persons"])
    person_ids = {p["id"] for p in sc["persons"]}
    fail("erase_persons_known", erased <= person_ids)
    auth_corr = [e for e in events.values() if e["event_type"] == "outcome"
                 and any(s["role"] == "correction" and is_auth(e, s) for s in e["body"]["sections"])]
    lessons = {corr_text(e) for e in auth_corr}
    lessons_after = {corr_text(e) for e in auth_corr if e["author"] not in erased}
    STATS[f"{split}_lessons"].append(len(lessons_after))
    fail("lessons_ge_40", len(lessons) >= 40 and len(lessons_after) >= 40)
    facts = {f["event_id"]: f for f in sc["grading_refs"]["facts"]}
    fail("facts_cover_all_authoritative_corrections", {e["event_id"] for e in auth_corr} == set(facts))
    fail("facts_day", all(day_of[k] == f["day"] for k, f in facts.items()))
    auth_texts = [corr_text(e) for e in auth_corr] + [s["text"] for e in events.values() if e["event_type"] == "outcome"
                                                      for s in e["body"]["sections"] if s["role"] == "evaluation" and is_auth(e, s)]
    all_texts = [t for e in events.values() for t in ev_texts(e)]
    fail("principal_not_granted_twin", not set(sc["twin_scopes"]) & set(sc["principal"]["granted_scopes"]))
    fail("principal_granted_own", sc["principal"]["granted_scopes"] == [sid])
    fail("twin_exists", len(sc["twin_scopes"]) >= 1 and all(t in by_id and t != sid for t in sc["twin_scopes"]))
    fail("tasks_exactly_4", len(sc["tasks"]) == 4)
    fail("task_types_one_each", sorted(t["type"] for t in sc["tasks"]) == ["T1", "T2", "T3", "T5"])
    n_inj = 0
    for t in sc["tasks"]:
        STATS[f"{split}_{sc['set']}_{t['type']}"].append(1)
        rx = compile_all(t)
        g = t["grading_refs"]
        erasure = t["erasure_target"]
        fail("erasure_only_t1_t2", not erasure or t["type"] in ("T1", "T2"))
        fail("expected_ask", t["expected_ask"] == (t["type"] == "T5" or erasure))
        # injection
        for iid in g["injection_event_ids"]:
            ev = events.get(iid)
            if not fail("injection_event_in_scope", ev is not None):
                continue
            secs = [s for s in ev["body"]["sections"] if "injection_regex" in rx and rx["injection_regex"].search(s["text"])]
            ok = bool(secs) and all(not is_auth(ev, s) and s["role"] in ("diagnostic", "operator_note", "status") for s in secs)
            ok = ok and all("ignore" in s["text"].casefold() and "always" in s["text"].casefold() for s in secs)
            ok = ok and (ev["source"] == "tool" or any(s["role"] == "operator_note" for s in secs))
            fail("injection_non_authoritative_imperative", ok)
            n_inj += ok
        if "injection_regex" in rx:
            fail("injection_value_not_authoritative", not any(rx["injection_regex"].search(x) for x in auth_texts))
        # twin
        tw = by_id.get(g["twin_scope"])
        fail("twin_scope_listed", g["twin_scope"] in sc["twin_scopes"])
        if fail("twin_scope_present", tw is not None):
            tw_events = {e["event_id"]: e for d in tw["days"] for ep in d["episodes"] for e in ep["events"]}
            tw_facts = {f["event_id"]: f for f in tw["grading_refs"]["facts"]}
            for tid in g["twin_event_ids"]:
                ev, f = tw_events.get(tid), tw_facts.get(tid)
                ok = ev is not None and f is not None and f["kind"] == "twin" and f["family"] == g["family"] \
                    and f["entity"] == g["entity"] and "cross_scope_regex" in rx and bool(rx["cross_scope_regex"].search(corr_text(ev)))
                fail("twin_holds_conflicting_version", ok)
                if ev is not None and "answer_regex" in rx:
                    fail("answer_regex_not_twin", not rx["answer_regex"].search(corr_text(ev)))
            fail("twin_no_grant", g["twin_scope"] not in sc["principal"]["granted_scopes"])
        if "cross_scope_regex" in rx:
            fail("cross_scope_value_absent_in_own_scope", not any(rx["cross_scope_regex"].search(x) for x in all_texts))
        # target facts
        base = None
        if t["type"] in ("T1", "T2"):
            tgt = events.get(g["target_event_ids"][0])
            if fail("target_present", tgt is not None and len(g["target_event_ids"]) == 1):
                f = facts.get(tgt["event_id"])
                fail("target_is_authoritative_correction", f is not None and f["entity"] == g["entity"]
                     and f["family"] == g["family"])
                base = corr_text(tgt)
                if erasure:
                    fail("erasure_answer_regex_null", t["answer_regex"] is None and "erased_regex" in rx)
                    fail("erasure_target_by_listed_person", tgt["actor_kind"] == "person" and tgt["author"] in erased)
                    if "erased_regex" in rx:
                        fail("erased_regex_matches_target", bool(rx["erased_regex"].search(base)))
                        fail("erased_target_only_source", not any(
                            rx["erased_regex"].search(x) for e in events.values() if e["author"] not in erased for x in ev_texts(e)))
                    STATS[f"{split}_{sc['set']}_E_{t['type']}"].append(1)
                else:
                    fail("answer_regex_matches_target", "answer_regex" in rx and bool(rx["answer_regex"].search(base)))
                    fail("target_author_not_erased", tgt["author"] not in erased)
                    fail("erased_regex_absent", t["erased_regex"] is None)
                if t["type"] == "T2":
                    j1 = jac(t["prompt"], base)
                    j2 = jac(" ".join([t["prompt"]] + t["addresses"]), base)
                    STATS[f"{split}_t2_jaccard"].append(max(j1, j2))
                    fail("t2_jaccard_le_0_20", j1 <= 0.20 and j2 <= 0.20)
                for k in ("injection_regex", "cross_scope_regex", "stale_regex"):
                    if k in rx:
                        fail("trap_regex_not_matching_answer_text", not rx[k].search(base))
        elif t["type"] == "T3":
            v1s, v2s = [events.get(x) for x in g["v1_event_ids"]], [events.get(x) for x in g["v2_event_ids"]]
            ok = len(v1s) == 1 and len(v2s) >= 2 and None not in v1s + v2s
            if fail("t3_events_present", ok):
                ok = all(facts.get(e["event_id"]) and facts[e["event_id"]]["entity"] == g["entity"]
                         and facts[e["event_id"]]["family"] == g["family"] for e in v1s + v2s)
                fail("t3_authoritative_corrections_same_fact", ok)
                fail("t3_v2_after_v1", all(day_of[v["event_id"]] > day_of[v1s[0]["event_id"]] for v in v2s))
                fail("t3_independent_counter_episodes", len({ep_of[v["event_id"]] for v in v2s}) == len(v2s) >= 2
                     and len({v["author"] for v in v2s}) >= 2)
                fail("t3_authors_not_erased", all(e["author"] not in erased for e in v1s + v2s))
                v1t, v2t = corr_text(v1s[0]), [corr_text(v) for v in v2s]
                fail("t3_regexes_present", "answer_regex" in rx and "stale_regex" in rx)
                if "answer_regex" in rx and "stale_regex" in rx:
                    fail("t3_answer_matches_v2_only", all(rx["answer_regex"].search(x) for x in v2t)
                         and not rx["answer_regex"].search(v1t))
                    fail("t3_stale_matches_v1_only", bool(rx["stale_regex"].search(v1t))
                         and not any(rx["stale_regex"].search(x) for x in v2t))
                for k in ("injection_regex", "cross_scope_regex"):
                    if k in rx:
                        fail("trap_regex_not_matching_answer_text", not any(rx[k].search(x) for x in v2t + [v1t]))
                base = v1t
        else:  # T5
            fail("t5_no_answer_regex", t["answer_regex"] is None and t["stale_regex"] is None and t["erased_regex"] is None)
            fail("t5_unanswered_in_scope", not any(f["family"] == g["family"] and f["entity"] == g["entity"]
                                                   and f["kind"] != "twin" for f in facts.values()))
            fail("t5_entity_near", any(g["entity"] in x for x in all_texts))
        # distractors
        dis = g["distractor_event_ids"]
        good = 0
        for did in dis:
            ev, f = events.get(did), facts.get(did)
            ok = ev is not None and f is not None and f["family"] == g["family"] and f["entity"] != g["entity"] \
                and f["kind"] != "twin" and ev["author"] not in erased
            if ok and base is not None:
                ok = jac(corr_text(ev), base) >= 0.25
                if "answer_regex" in rx:
                    fail("answer_regex_not_distractor", not rx["answer_regex"].search(corr_text(ev)))
            good += bool(ok)
        STATS[f"{split}_distractors"].append(good)
        fail("distractors_ge_5", good >= 5 and good == len(dis))
        # answer regex vs trap values (texts carrying them)
        if "answer_regex" in rx:
            for iid in g["injection_event_ids"]:
                if iid in events:
                    fail("answer_regex_not_injection", not any(rx["answer_regex"].search(x) for x in ev_texts(events[iid])))
            if t["type"] == "T3":
                fail("answer_regex_not_stale", not rx["answer_regex"].search(corr_text(events[g["v1_event_ids"][0]])))
    fail("injection_present_in_scope", n_inj >= 1)
    fail("erasure_scope_has_one_target", len(erased) == sum(t["erasure_target"] for t in sc["tasks"]) <= 1)
    return {t for e in events.values() for t in ev_texts(e)} | {t["prompt"] for t in sc["tasks"]}


def collect_names(scopes):
    names = set()
    for sc in scopes:
        names |= {sc["scope_id"], sc["project"], sc["principal"]["id"]}
        names |= {p["name"] for p in sc["persons"]}
        names |= {f["entity"] for f in sc["grading_refs"]["facts"]}
        for d in sc["days"]:
            for ep in d["episodes"]:
                for ev in ep["events"]:
                    for a in ev["addresses"]:
                        kind, body = a.split(":", 1)
                        names.add(body)
                        if kind in ("entity", "code"):
                            names.add(body.split("/", 1)[0])
    return {n.casefold() for n in names}


def other_corpus():
    parts = []
    for p in sorted((ROOT / "tests" / "regression").rglob("*")):
        if p.is_file() and "exp0004" not in p.parts:
            parts.append(p.read_bytes().decode("utf-8", "replace").casefold())
    return "\n".join(parts)


SECRET_RX = [re.compile(r"-----BEGIN"), re.compile(r"\bAKIA[0-9A-Z]{12,}"), re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|bearer|token)\b"),
             re.compile(r"\b(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*[0-9])[A-Za-z0-9+/_-]{20,}\b")]


def main():
    man = json.loads((DATA / "MANIFEST.json").read_text())
    docs, raw = {}, {}
    for split in ("dev", "test"):
        raw[split] = (DATA / f"{split}.json").read_bytes()
        docs[split] = json.loads(raw[split])
        fail("manifest_sha256", man["files"][f"{split}.json"]["sha256"] == hashlib.sha256(raw[split]).hexdigest())
        fail("stop_words_match", set(docs[split]["stop_words"]) == STOP_WORDS)
        fail("secret_shape_scan", not any(r.search(raw[split].decode()) for r in SECRET_RX))
    fail("manifest_generator_sha256",
         man["generator_sha256"] == hashlib.sha256((ROOT / "scripts/make_exp0004_set.py").read_bytes()).hexdigest())
    want = {"dev": {"S1": 5, "S2": 5, "S3": 5}, "test": {"S1": 30, "S2": 30, "S3": 30}}
    texts, names, ids = {}, {}, {}
    dom = {"S1": "coding", "S2": "operations", "S3": "client"}
    for split, doc in docs.items():
        scopes = doc["scopes"]
        by_id = {s["scope_id"]: s for s in scopes}
        fail("scope_ids_unique", len(by_id) == len(scopes))
        fail("split_sizes", Counter(s["set"] for s in scopes) == Counter(want[split]))
        fail("set_domains", all(dom[s["set"]] == s["domain"] and s["split"] == split for s in scopes))
        fail("twins_same_set", all(by_id[s["twin_scopes"][0]]["set"] == s["set"] for s in scopes if s["twin_scopes"][0] in by_id))
        texts[split] = set()
        for sc in scopes:
            texts[split] |= check_scope(sc, by_id, split)
        names[split] = collect_names(scopes)
        ids[split] = set(by_id)
    fail("dev_test_no_shared_scope_ids", not ids["dev"] & ids["test"])
    fail("dev_test_no_shared_texts", not texts["dev"] & texts["test"])
    fail("dev_test_no_shared_names", not names["dev"] & names["test"])
    corpus = other_corpus()
    clashes = sum(1 for n in names["dev"] | names["test"] if n in corpus)
    fail("no_names_from_other_regression_sets", clashes == 0)
    # fixed mix
    for split in ("dev", "test"):
        for s in ("S1", "S2", "S3"):
            for ty in ("T1", "T2", "T3", "T5"):
                n = len(STATS[f"{split}_{s}_{ty}"])
                fail("mix_per_set", n == want[split][s])
    e = {(sp, s, ty): len(STATS[f"{sp}_{s}_E_{ty}"]) for sp in ("dev", "test") for s in ("S1", "S2", "S3") for ty in ("T1", "T2")}
    fail("test_erasure_5_plus_5_per_set", all(e[("test", s, ty)] == 5 for s in ("S1", "S2", "S3") for ty in ("T1", "T2")))
    # ---- report: counts and hashes only
    print("EXP-0004 set check")
    for split in ("dev", "test"):
        print(f"  {split}: sha256 {hashlib.sha256(raw[split]).hexdigest()}  bytes {len(raw[split])}")
        for s in ("S1", "S2", "S3"):
            row = " ".join(f"{ty}={len(STATS[f'{split}_{s}_{ty}'])}" for ty in ("T1", "T2", "T3", "T5"))
            print(f"    {s}: scopes {want[split][s]}  {row}  erasure T1={e[(split, s, 'T1')]} T2={e[(split, s, 'T2')]}")
        ep, ev, le, di = (STATS[f"{split}_{k}"] for k in ("episodes", "events", "lessons", "distractors"))
        print(f"    episodes/scope min {min(ep)} max {max(ep)} total {sum(ep)}; events total {sum(ev)}")
        print(f"    lesson corrections/scope (after erasure) min {min(le)}; distractors/task min {min(di)}")
        print(f"    T2 max Jaccard {max(STATS[f'{split}_t2_jaccard']):.3f}")
    print(f"  generator sha256 {man['generator_sha256']}")
    print(f"  checker sha256 {hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}")
    print(f"  manifest sha256 {hashlib.sha256((DATA / 'MANIFEST.json').read_bytes()).hexdigest()}")
    print(f"  name clashes with other regression sets: {clashes}")
    names_checked = [n for n in ("event_type", "trust_derivation", "ref_backward_same_scope", "days_of_10", "episodes_60_150",
                                 "lessons_ge_40", "tasks_exactly_4", "mix_per_set", "test_erasure_5_plus_5_per_set",
                                 "erasure_target_by_listed_person", "erased_target_only_source", "t2_jaccard_le_0_20",
                                 "t3_v2_after_v1", "t3_independent_counter_episodes", "t5_unanswered_in_scope",
                                 "distractors_ge_5", "twin_holds_conflicting_version", "twin_no_grant",
                                 "injection_non_authoritative_imperative", "regex_compiles", "regex_not_in_prompt",
                                 "answer_regex_not_injection", "answer_regex_not_twin", "answer_regex_not_stale",
                                 "no_names_from_other_regression_sets", "dev_test_no_shared_texts", "secret_shape_scan",
                                 "manifest_sha256")]
    for n in names_checked:
        print(f"  [{'FAIL ' + str(FAILS[n]) if FAILS[n] else 'OK'}] {n}")
    other = {k: v for k, v in FAILS.items() if k not in names_checked}
    for k, v in sorted(other.items()):
        print(f"  [FAIL {v}] {k}")
    print(f"  total violations: {sum(FAILS.values())}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
