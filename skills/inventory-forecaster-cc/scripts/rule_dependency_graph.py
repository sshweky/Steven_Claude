#!/usr/bin/env python3
"""
rule_dependency_graph.py  --  Static analysis of rule -> rule dependencies.

Parses inventory_forecaster.py via AST and extracts:
  - Every rule code fire site (_fire("Fxx") and driver-string emissions)
  - Every meta[...] read and write surrounding each fire site
  - Every fcst[...] write site
  - Which rules read meta keys that other rules write (= cross-rule
    dependency edge that has implicit ordering risk)

Outputs:
  - rule_deps.md     -- markdown table of rule | reads | writes
  - rule_deps.dot    -- Graphviz DOT graph (render with `dot -Tpng rule_deps.dot`)

Why this matters:
  Right now forecast_record() is a 3,150-line waterfall with implicit
  ordering invariants. If F59a reads meta["f18_capped_down"] but F18 was
  reordered to fire later, F59a silently uses stale state. This graph
  surfaces those edges so any reordering can be cross-checked first.

Known limitation:
  Edge detection uses a line-window heuristic (default +/- 15 lines around
  each fire site). Reads outside that window are missed. A proper AST
  scope analysis (track if/elif blocks per rule) is a Phase 2 follow-up.
  The rule-to-meta-key matrix is still valuable on its own -- it tells
  you which keys each rule WRITES, which is the harder half of the
  dependency question.

Usage:
    python scripts/rule_dependency_graph.py
    python scripts/rule_dependency_graph.py --top 20   # only show top 20 highest-degree rules
"""

import ast
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# Rule code recognition (same as audit_rules.py)
# ─────────────────────────────────────────────────────────────────────────────
_RULE_CODE_FAMILIES = re.compile(
    r'''^(?:
        F\d+[a-z]?                |
        F\d+-[A-Za-z]+            |
        F_[A-Z_]+                 |
        R\d+                      |
        M\d+                      |
        S\d+                      |
        T\d+                      |
        VP-Q\d+                   |
        VP-[A-Z]+(?:-[A-Za-z]+)?  |
        G\d+
    )$''',
    re.VERBOSE,
)


def is_rule_code(s: str) -> bool:
    return bool(_RULE_CODE_FAMILIES.match(s or ""))


# Look for rule code at the start of any string literal, e.g. "F18 ..." or "VP-Q4 W{w}".
_LEADING_RULE_RE = re.compile(r'^([A-Za-z][A-Za-z0-9_\-]*?)\s')


def extract_leading_rule(s: str) -> str | None:
    """Return the leading rule code from a string literal, or None."""
    if not isinstance(s, str):
        return None
    m = _LEADING_RULE_RE.match(s)
    if m and is_rule_code(m.group(1)):
        return m.group(1)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# AST walker -- maps each AST node to its enclosing rule (if any).
# A "rule context" is the rule code most recently mentioned in a sibling
# string literal or _fire() call within the current function body.
# ─────────────────────────────────────────────────────────────────────────────


class RuleScopeVisitor(ast.NodeVisitor):
    """Walk the AST. For every fire site, record the surrounding meta[] and
    fcst[] reads/writes that fall within ~50 lines of the fire site.
    """

    def __init__(self, src_lines):
        self.src_lines = src_lines
        # rule -> set of meta keys it reads
        self.reads  = defaultdict(set)
        # rule -> set of meta keys it writes (assignment or .setdefault().append())
        self.writes = defaultdict(set)
        # rule -> set of "fcst[w] = ..." line markers (count, not specific weeks)
        self.fcst_writes = defaultdict(int)
        # rule -> set of fire line numbers
        self.fires       = defaultdict(set)
        # All meta key reads/writes seen (line -> kind -> key)
        self._meta_reads_by_line  = defaultdict(set)
        self._meta_writes_by_line = defaultdict(set)
        self._fcst_writes_by_line = defaultdict(int)

    def visit_Subscript(self, node):
        """meta["foo"] or fcst[w]"""
        if (isinstance(node.value, ast.Name)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            container = node.value.id
            key       = node.slice.value
            if container == "meta":
                if isinstance(node.ctx, ast.Store):
                    self._meta_writes_by_line[node.lineno].add(key)
                else:
                    self._meta_reads_by_line[node.lineno].add(key)
        # fcst[w] = ...  (w is dynamic, just count)
        if (isinstance(node.value, ast.Name) and node.value.id == "fcst"
                and isinstance(node.ctx, ast.Store)):
            self._fcst_writes_by_line[node.lineno] += 1
        self.generic_visit(node)

    def visit_Call(self, node):
        # meta.get("key") / meta.setdefault("key", ...) — both count as a read.
        # meta.setdefault(...) ALSO counts as a write (it may insert the key).
        # meta.update({...}) — count keys as writes.
        if isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "meta"):
                attr = node.func.attr
                if attr in ("get", "setdefault", "pop") and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        self._meta_reads_by_line[node.lineno].add(a0.value)
                        if attr == "setdefault":
                            self._meta_writes_by_line[node.lineno].add(a0.value)
                if attr == "update" and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Dict):
                        for k in a0.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                self._meta_writes_by_line[node.lineno].add(k.value)

        # _fire("Fxx")
        if (isinstance(node.func, ast.Name) and node.func.id == "_fire"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and is_rule_code(node.args[0].value)):
            self.fires[node.args[0].value].add(node.lineno)
        # meta.setdefault("drivers", []).append("Fxx ...")
        # meta["drivers"].append(f"Fxx ...")
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                and node.args):
            arg0 = node.args[0]
            # Plain string
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                code = extract_leading_rule(arg0.value)
                if code:
                    self.fires[code].add(node.lineno)
            # f-string -- look at the first FormattedValue or Str part
            if isinstance(arg0, ast.JoinedStr):
                for v in arg0.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        code = extract_leading_rule(v.value)
                        if code:
                            self.fires[code].add(node.lineno)
                        break
        self.generic_visit(node)


def attribute_meta_to_rule(visitor: RuleScopeVisitor, window: int = 30):
    """Walk every fire site. For each, attribute meta reads/writes within
    +/- `window` lines to that rule. fcst writes attributed similarly."""
    for rule, fire_lines in visitor.fires.items():
        for fl in fire_lines:
            lo, hi = fl - window, fl + window
            for ln, keys in visitor._meta_reads_by_line.items():
                if lo <= ln <= hi:
                    visitor.reads[rule].update(keys)
            for ln, keys in visitor._meta_writes_by_line.items():
                if lo <= ln <= hi:
                    visitor.writes[rule].update(keys)
            for ln, count in visitor._fcst_writes_by_line.items():
                if lo <= ln <= hi:
                    visitor.fcst_writes[rule] += count


# Keys that every rule reads/writes (just for narrative). Excluding these
# removes ~99% of edges and leaves only the semantically interesting ones.
_TRIVIAL_META_KEYS = {"drivers", "rule_fires", "baseline_mode"}


def build_edges(visitor: RuleScopeVisitor) -> list[tuple[str, str, str]]:
    """Edge = (writer_rule, reader_rule, shared_meta_key). Filters trivial keys."""
    edges = []
    for reader, read_keys in visitor.reads.items():
        interesting_reads = read_keys - _TRIVIAL_META_KEYS
        if not interesting_reads:
            continue
        for writer, write_keys in visitor.writes.items():
            if writer == reader:
                continue
            shared = interesting_reads & (write_keys - _TRIVIAL_META_KEYS)
            for k in shared:
                edges.append((writer, reader, k))
    return edges


def write_markdown(visitor: RuleScopeVisitor, edges: list, out: Path,
                   top: int | None = None):
    rules = sorted(visitor.fires.keys(),
                   key=lambda r: -(len(visitor.reads[r]) + len(visitor.writes[r])
                                   + visitor.fcst_writes[r]))
    if top:
        rules = rules[:top]

    with open(out, "w", encoding="utf-8") as f:
        f.write("# Rule Dependency Graph\n\n")
        f.write("Generated by `scripts/rule_dependency_graph.py`. Re-run after rule changes.\n\n")
        f.write(f"**{len(visitor.fires)} rules detected**, **{len(edges)} cross-rule edges**.\n\n")
        f.write("## Rule -> meta-key matrix\n\n")
        f.write("| Rule | Fires (lines) | Reads | Writes | fcst writes |\n")
        f.write("|------|---------------|-------|--------|-------------|\n")
        for r in rules:
            fls = sorted(visitor.fires[r])
            fl_str = ", ".join(f"L{ln}" for ln in fls[:3])
            if len(fls) > 3:
                fl_str += f" (+{len(fls)-3})"
            reads_str = ", ".join(sorted(visitor.reads[r])) or "-"
            writes_str = ", ".join(sorted(visitor.writes[r])) or "-"
            fc = visitor.fcst_writes[r] or "-"
            f.write(f"| `{r}` | {fl_str} | `{reads_str[:80]}` | `{writes_str[:80]}` | {fc} |\n")

        f.write("\n## Cross-rule dependency edges\n\n")
        f.write("These edges show implicit ordering: `WRITER` must fire before `READER`,\n")
        f.write("or `READER` silently sees stale state.\n\n")
        if not edges:
            f.write("_(none detected within the analysis window)_\n")
        else:
            f.write("| Writer | -> | Reader | Shared meta key |\n")
            f.write("|--------|----|--------|------------------|\n")
            for w, r, k in sorted(edges):
                f.write(f"| `{w}` | -> | `{r}` | `{k}` |\n")


def write_dot(visitor: RuleScopeVisitor, edges: list, out: Path,
              top: int | None = None):
    rules = sorted(visitor.fires.keys(),
                   key=lambda r: -(len(visitor.reads[r]) + len(visitor.writes[r])))
    if top:
        rules = set(rules[:top])
    else:
        rules = set(rules)

    with open(out, "w", encoding="utf-8") as f:
        f.write("digraph rule_deps {\n")
        f.write('  rankdir=LR;\n')
        f.write('  node [shape=box, style=rounded, fontname="Helvetica"];\n')
        for r in rules:
            label = (f"{r}\\n"
                     f"reads={len(visitor.reads[r])} writes={len(visitor.writes[r])}\\n"
                     f"fcst_writes={visitor.fcst_writes[r]}")
            f.write(f'  "{r}" [label="{label}"];\n')
        for w, r, k in edges:
            if w in rules and r in rules:
                f.write(f'  "{w}" -> "{r}" [label="{k}"];\n')
        f.write("}\n")


def main():
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default=str(here / "inventory_forecaster.py"))
    p.add_argument("--md-out", default=str(here.parent / "rule_deps.md"))
    p.add_argument("--dot-out", default=str(here.parent / "rule_deps.dot"))
    p.add_argument("--window", type=int, default=15,
                   help="Line window (each side of fire site) for attributing "
                        "meta reads/writes to a rule. Default 15.")
    p.add_argument("--top", type=int, default=None,
                   help="Only include the N highest-degree rules in the output.")
    args = p.parse_args()

    src_path = Path(args.source)
    print(f"Parsing {src_path} ...")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    visitor = RuleScopeVisitor(src.splitlines())
    visitor.visit(tree)
    attribute_meta_to_rule(visitor, window=args.window)
    edges = build_edges(visitor)

    print(f"  Found {len(visitor.fires)} distinct rule codes "
          f"({sum(len(s) for s in visitor.fires.values())} fire sites).")
    print(f"  Meta reads attributed to {len(visitor.reads)} rules.")
    print(f"  Meta writes attributed to {len(visitor.writes)} rules.")
    print(f"  Cross-rule dependency edges: {len(edges)}")

    write_markdown(visitor, edges, Path(args.md_out), top=args.top)
    print(f"  Wrote {args.md_out}")
    write_dot(visitor, edges, Path(args.dot_out), top=args.top)
    print(f"  Wrote {args.dot_out}  (render: dot -Tpng {args.dot_out} -o rule_deps.png)")


if __name__ == "__main__":
    main()
