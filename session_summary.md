# Session summary — z3 interval edge colouring: explanation, optimizations, visualization

## 1. How the section-3 solver works (`interval_coloring` in `faster_version_reencoded.ipynb`)

Solves *interval edge colouring* of a graph: assign each edge a colour so that at every
vertex, incident edges get (a) all-distinct colours and (b) colours forming a
consecutive run with no gaps. Steps:

1. One z3 `Int` variable per edge.
2. Build a vertex → incident-edge-indices map.
3. `delta` = max vertex degree = hard lower bound on colours needed.
4. Build constraints **once**: `Distinct` per vertex (condition 1), plus a per-vertex
   auxiliary `lo` ("window start") variable with `lo <= x <= lo + degree - 1` for each
   incident edge (condition 2). `Distinct` + a width-`degree` window together force
   consecutiveness without expensive nested `If` terms — this was the notebook's
   existing "point 2" optimization.
5. Loop `k = delta, delta+1, ...`: `push()`, add `x <= k-1` for all edges, `check()`.
   If `sat`, extract the model and return; else `pop()` and try the next `k`. Reusing
   one `Solver` across all `k` (instead of rebuilding per `k`) was "point 1".

## 2. Z3 guide review → 5 more optimization ideas

Fetched https://theory.stanford.edu/~nikolaj/programmingz3.html and cross-checked
suggestions against known z3 behavior. Recommended, in order of confidence:

1. **Assumptions instead of `push()`/`pop()`** — pass `x <= k-1` list directly to
   `s.check([...])` instead of push/add/check/pop. Same semantics, less overhead.
2. **`BitVec` instead of `Int`** — bounded domain + disequality-heavy (`Distinct`)
   problem is exactly where bit-blasting to SAT beats `Int`'s Simplex theory, which
   case-splits on every disequality. Biggest expected win.
3. **Profile with `Solver.statistics()`** — not a speedup itself, but shows where
   effort (decisions/conflicts/propagations/restarts) actually goes before optimizing
   further.
4. **`Optimize()`/`minimize()`** instead of a manual k-loop — the base constraints
   don't depend on k, so minimizing `max(colours)` directly can also short-circuit to
   `unsat` immediately on wholly non-colourable graphs (no need to exhaust a guessed
   `max_colors` first). Uncertain whether it beats the manual loop on hard *sat*
   instances — needs benchmarking, not assumed.
5. **Symmetry breaking** — colours are only meaningful up to a constant shift, so
   `Or([x == 0 for x in xs])` (pin the minimum colour to 0) loses no solutions and
   cuts redundant search.

## 3. Visualizing the solver's search — clarified expectations

Clarified a likely misconception before building anything: z3's CDCL/SMT search does
**not** build up a solution incrementally like a "half-colored graph" — it
decides → propagates → conflicts → backtracks → learns, non-monotonically, and
there's no API to peek at a live partial assignment mid-`check()`. What *is*
real and visualizable: per-`k` aggregate effort (time, decisions, conflicts,
propagations, restarts) from `Solver.statistics()`, captured after each `check()`
call across the `k = Delta, Delta+1, ...` search.

## 4. Changes made

### `faster_version_reencoded.ipynb` — added section "4b. Per-k solver effort"
(inserted right after section 4 "Run", before section 5 "Draw")
- `interval_coloring_instrumented(graph, max_colors=None)` — same search as
  `interval_coloring`, but records `time`, `decisions`, `conflicts`, `propagations`,
  `restarts` per `k` tried.
- A runner/plot cell: prints a per-k table and draws two stacked bar charts (time per
  k; decisions/conflicts per k), colour-coded green=sat / red=unsat.
- Verified: function logic and plot cell both confirmed to execute correctly.

### New file: `optimized.ipynb`
Implements and benchmarks all 5 optimizations above, each as an isolated variant of
the baseline (so only one technique differs at a time):
- `interval_coloring_baseline` — the starting point (= original `interval_coloring`)
- `interval_coloring_assumptions` — optimization 1
- `interval_coloring_bitvec` — optimization 2 (with explicit `[0, max_colors-1]`
  fencing on both edge and `lo` variables to rule out BitVec wraparound producing
  spurious "consecutive" windows — a real correctness risk that needed handling, not
  just a naive `Int`→`BitVec` swap)
- `interval_coloring_profiled` — optimization 3
- `interval_coloring_optimize` — optimization 4
- `interval_coloring_symmetry` — optimization 5
- `interval_coloring_combined` — 1+2+5 stacked together
- `verify_interval` + a correctness-check cell comparing all variants (incl.
  `Optimize()`) against the baseline across 4 test graphs — all agree, all solutions
  independently valid.
- A benchmark cell on the harder `K(2,2,3)` graph (capped at 9 colours, all unsat):

  | variant | time | speedup |
  |---|---|---|
  | baseline | ~18–22s | 1x |
  | assumptions | ~11–12s | ~1.7–1.8x |
  | symmetry | ~17s | ~1.1–1.3x |
  | bitvec | ~5s | ~3.7–4.4x |
  | **combined** | **~1.5–1.7s** | **~11–15x** |

  (Exact numbers vary by run/machine; the *ordering* — bitvec is the single biggest
  lever, combining compounds rather than adds — is the finding.)
- Whole notebook executed end-to-end via `jupyter nbconvert --execute` with no errors
  before being finalized.

## 5. `optimized.ipynb` → renamed `sat_unsat.ipynb`; matrix-save + drawing + HTML added

`optimized.ipynb` (the notebook from section 4 keeping only the combined
BitVec + assumptions + symmetry-breaking encoding) was renamed to
`sat_unsat.ipynb` to distinguish it from the new `Optimize()`-based notebook
in section 7. Ported over from `faster_version_reencoded.ipynb`, adapted to
that notebook's `G`/`gname` variable names:
- **§3 Solution → matrix**: `color_matrix`/`format_matrix`/`format_kmn`/
  `render_matrix`/`log_solution` — appends each run's stats + solution matrix
  to `solutions.txt`.
- **§5 Draw the coloured graph**: same row-per-part pygraphviz layout as the
  base notebook (smallest part pinned top, largest pinned bottom, edges
  coloured + labelled by colour number).
- **§6 Save to HTML**: appends stats + matrix + base64 PNG to `solutions.html`.

## 6. More optimization ideas from `programmingz3.html` — tested, none beat the current encoding

Benchmarked three further ideas from the tutorial against the notebook's own
`K(2,2,3)`-capped-at-9 benchmark (forces a full unsat sweep, ~1.0-1.1s
baseline):

1. **Custom tactic pipelines** (`Then('simplify','bit-blast','sat').solver()`,
   `Tactic('qfbv').solver()`) — the raw `bit-blast`→`sat` pipeline fails
   outright: `Distinct` over `BitVec`s isn't blastable without an elimination
   pass first (`unknown`, "operator ... not supported, apply simplifier
   before invoking translator"). `Tactic('qfbv').solver()` just ties the
   plain `Solver()` (~1.15s vs ~1.10s) — z3's default already picks an
   equivalent strategy for this problem class.
2. **Z3's internal SAT parallelism** (`set_param('sat.threads', n)`) — no
   measurable difference at 1/2/4 threads (~1.15-1.19s across the board);
   instances are too small for cube-and-conquer to pay off.
3. **Parallelizing across `k`** (checking independent `k` values concurrently
   in Python threads, since each unsat proof is independent work) —
   **segfaults**. Z3's default `Context` is not thread-safe for concurrent
   `check()` calls; would need per-thread `Context()` objects and expression
   translation to attempt safely, and still likely wouldn't beat ~1.1s at
   this instance size. Not pursued further.

Conclusion: nothing on that page beats what `sat_unsat.ipynb` already does.

## 7. Turning the problem into an *optimization* problem: `Optimize()`/`minimize()`

Section 8 of `programmingz3.html` (Optimize services / OMT) prompted
re-testing the `Optimize()` variant from section 4's original 5-idea list —
previously flagged "uncertain, needs benchmarking," never resolved. Now
benchmarked properly:

| case | manual k-sweep | `Optimize()` |
|---|---|---|
| K(2,2,3), capped @9 (unsat, must exhaust all k) | 1.10s | **0.38s** (~2.9x) |
| K(2,2,3), full range (unsat) | 1.04s | **0.37s** (~2.8x) |
| K(1,2,3) (sat @k=5, first try) | 0.007s | 0.027s |
| K(2,2,2) (sat @k=4, first try) | 0.006s | 0.019s |
| K(2,2,4) (sat @k=6, first try) | 0.020s | 0.080s |

**Why it wins on hard (unsat) instances — structural, not just engine
cleverness.** The manual sweep proves `unsat` independently at *every* `k`
from `Delta` upward. But the constraint at `k` is strictly tighter than at
`k+1` (smaller `k` = smaller upper bound on colours), so if the *loosest*
bound (`k = max_colors`) is already infeasible, every tighter `k` is
automatically infeasible too — no sweep needed. `Optimize()` only ever
asserts that one loosest bound as a hard constraint (`M <= max_colors - 1`)
plus `minimize(M)`, so a fully-uncolourable graph collapses N sweep-checks
into a single proof. On easy instances where the very first `k` tried is
already `sat`, `Optimize()` is slightly slower (tens of ms) because it must
additionally *prove* optimality (rule out `M < delta`) rather than stopping
at the first model — negligible next to the multi-second unsat cases that
actually matter for benchmarking.

## 8. Trusting the optimum — verification strategy

"Optimal" is a stronger claim than "satisfiable," so it isn't taken on
`Optimize()`'s word alone. Three independent, empirically-confirmed layers:

1. **`obj.lower() == obj.upper()`** after `check()` — Z3's own bound
   bookkeeping; equal bounds is *itself* the proof search converged (no
   timeout was set, so `sat` implies proven-optimal, not best-effort).
2. **Bracket check** (`verify_minimum`): a completely fresh, independent
   `Solver()` — sharing no state with the `Optimize()` search — checks that
   `colours - 1` is `unsat`. Confirmed `unsat` on all tested cases (e.g.
   K(1,2,3): optimum 4, bracket-checked `colours <= 3` → unsat; K(2,2,2):
   optimum 3, bracket-checked `colours <= 2` → unsat).
3. **`verify_interval`**: pure-Python (no z3) check that the returned
   solution is actually a valid interval colouring (distinct + consecutive
   at every vertex). Guards against "optimal but wrong," independent of (1)
   and (2), which only guard against "wrong optimum."

## 9. New file: `optimize.ipynb`

Implements the `Optimize()`-based solver as its own notebook (parallel to
`sat_unsat.ipynb`, not a replacement):
- `interval_coloring_optimize` — BitVec colours + minimum-colour-is-0
  symmetry breaking (kept from the winning combined encoding) + single
  `Optimize()` call with `minimize(M)` instead of a k-sweep. Returns
  `lower`/`upper` bounds alongside the usual result fields.
- `verify_interval` + `verify_minimum` — the two independent checks from
  section 8, run automatically in the "Run" cell and asserted on failure.
  `verify_minimum` returns `None` (skipped, printed as N/A) when `colours`
  already equals `delta`, the hard lower bound — nothing tighter to disprove.
- Matrix rendering (`color_matrix`/`format_matrix`/`format_kmn`/
  `render_matrix`) reused as-is; **no `log_solution`/`solutions.txt`** in
  this notebook per request — HTML output only.
- Same row-layout drawing section as `sat_unsat.ipynb`.
- Saves to **`solutions_optimize.html`**, a separate file from
  `sat_unsat.ipynb`'s `solutions.html` so the two notebooks' saved runs don't
  mix.
- Executed end-to-end via `jupyter nbconvert --execute` with no errors;
  verified output on `K(1,1,2)`: optimum 3 colours, `lower=upper=2`
  (0-indexed M), `verify_interval` True, `verify_minimum` N/A (3 == Delta).

## 10. How `optimize.ipynb` solves the problem — detailed walkthrough

`interval_coloring_optimize(graph, max_colors=None)` (cell `8826058a`). Same
problem as everywhere else in this project — interval edge colouring: every
edge gets a colour such that at each vertex, incident edges are all-distinct
*and* form a gap-free consecutive run — but encoded and solved differently
from the `sat_unsat.ipynb` sweep.

**1. Variables.** One `BitVec('e%d' % i, width)` per edge (not `Int`, not a
fresh var set per `k` — width is fixed once via
`width = max(4, (2 * max_colors).bit_length() + 2)`, sized with headroom so
no arithmetic on colour values can wrap around the fixed bit width).

**2. Hard bounds.** Every edge variable is fenced to the full candidate range
up front: `x >= 0, x <= max_colors - 1`. Unlike the sweep, there is no
tighter per-`k` cap added later — the *only* thing that narrows the answer
down from this loose range is the objective (step 5).

**3. Symmetry breaking.** `Or([x == 0 for x in xs])` — forces at least one
edge to literally take colour 0. Interval colourings are only meaningful up
to a constant shift (shift every colour by the same amount and it's still
valid), so without this the search wastes time exploring an entire family of
shifted-but-equivalent solutions per real solution. This pins the search to
one canonical (zero-based) representative of each family.

**4. The colouring constraints.** Per vertex `v` with incident edge
variables `vs`: `Distinct(vs)` (all different) plus a per-vertex auxiliary
`lo = BitVec('lo_%s' % v, width)`, fenced to `[0, max_colors-1]`, with
`x >= lo, x <= lo + (len(vs)-1)` for every `x` in `vs`. `Distinct` plus a
window of width `len(vs)` that must hold `len(vs)` distinct values forces
those values to be exactly one consecutive run — same trick as
`sat_unsat.ipynb`, ported over unchanged.

**5. The objective — what actually replaces the k-sweep.** One extra
`BitVec('M', width)`, with `x <= M` asserted for every edge variable (M is an
upper bound on every colour used) and the loosest possible cap
`M <= max_colors - 1`. Then `obj = o.minimize(M)` registers M as the quantity
to minimize. This is the entire replacement for the Python
`for k in range(delta, max_colors+1): ...` loop in `sat_unsat.ipynb` — instead
of Python re-asserting a tighter `x <= k-1` and re-checking per candidate,
one `o.check()` call hands the whole "find the smallest feasible upper bound"
problem to z3's optimization engine.

**6. What `o.check()` actually does internally.** Unlike a plain
`Solver.check()`, which stops at the first satisfying model, `Optimize()`
with a `minimize()` objective keeps going after finding a model: it takes the
current model's value of `M`, asserts an internal constraint ruling out that
value or worse, and re-checks — repeating until the solver comes back
`unsat` on "strictly better than my best model so far," which is the proof
that the last model found was optimal. All of this happens inside the single
`o.check()` call; `check()` doesn't return until optimality is proven (no
timeout is set here, so `sat` on return means *proven* optimal, not
best-effort). `obj.lower()`/`obj.upper()` afterwards expose z3's own
converged bounds on `M` — equal to each other iff optimality was proven.

**7. Reading out the answer.** On `sat`: pull the model, read `m[xs[idx[e]]]`
per edge for the solution dict, and `k = m[M].as_long() + 1` (M is the
0-indexed max colour used, so colour *count* is M+1). On `unsat` (only
possible if even `max_colors` colours can't colour the graph): `colorable`
comes back `False` with `solution=None`.

**8. Progress instrumentation (added this session).** Because step 6 happens
entirely inside one blocking `check()` call, there is no per-candidate loop
in Python to hang stats off of the way `sat_unsat.ipynb`'s `_profile_search`
does. The fix: `Optimize.set_on_model(callback)` registers a callback z3
invokes itself every time step 6's internal search lands on a new improving
model, before it's proven optimal. `interval_coloring_optimize` now passes
an `on_model` closure that reads `m[M].as_long() + 1` and `time.time() - t0`
into a `progress` list at each call, then returns `(res, progress)` instead
of just `res` (the run cell was updated to unpack the tuple). Gotcha: per
the z3 docs, the model handed to the callback is only valid *inside* the
callback (its lifetime is tied to the callback invocation) — the callback
must pull out plain values (`.as_long()`) immediately rather than storing
the `Model` object itself. Smoke-tested on `K(1,1,2)`: progress log showed
the bound tightening 6 → 4 → 3 before `Optimize()` settled on and proved the
optimum of 3. A new section "4b. Optimize() progress" plots this as a step
chart (colours vs. wall-clock time) — the `Optimize()` analogue of
`sat_unsat.ipynb`'s per-k bar chart, but driven by improving-bound events
instead of discrete `k` iterations.

**9. Trust layer on top.** Steps 1–8 only establish what `Optimize()` itself
believes; section 8 above (`verify_interval`, `verify_minimum`) independently
re-checks the result without depending on `Optimize()`'s own bookkeeping —
see that section for why both checks exist and what each one guards against.

## 11. `sat_unsat.ipynb` — added section "4b. Per-k solver effort" (this session)

Mirrors what `faster_version_reencoded.ipynb` already had: `stats` captured
by `interval_coloring_profiled`/`_profile_search` (`k`, `sat`, `time`,
`decisions`, `conflicts`, `propagations`, `restarts` per `k` tried) plotted
as two stacked bar charts — time per `k`, and decisions/conflicts per `k` —
colour-coded green=`sat`/red=`unsat`. Confirms visually what section 3's
markdown already states in words: on hard instances almost all the bar
height should be red, since proving a `k` too small is what costs time, not
the eventual `sat` call.

## Notes / gotchas hit this session
- Writing into `~/.claude/graph-coloring/` required disabling the sandbox
  (`dangerouslyDisableSandbox`) — the default sandbox blocks writes there.
- `Solver.statistics()` keys (e.g. `conflicts`) only appear once the solver actually
  does that kind of work — trivial instances may lack keys like `conflicts` entirely;
  code should `.get(key, 0)` defensively.
- Raw `Then('simplify', 'bit-blast', 'sat').solver()` pipelines can't handle
  `Distinct` directly over `BitVec`s — need an elimination/simplification
  pass first, or stick with the default `Solver()`/`Tactic('qfbv')`.
- z3's default `Context` is **not thread-safe** for concurrent `check()`
  calls from Python threads — naive "parallelize across k" attempts segfault.
  Per-thread `Context()` + expression translation would be required to do
  this safely, and wasn't worth the complexity at this problem size.
- `Optimize()` objective handles expose `.lower()`/`.upper()` (both
  `.as_long()`-able for `BitVec` objectives) — a converged (equal) pair after
  `check()` is a free, built-in optimality signal, no extra solving needed.
