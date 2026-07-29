"""Parallel interval edge-colouring search.

One OS process per candidate colour-count k, all launched at once. Sidesteps
Python's GIL (Z3's check() holds it) by using processes, not threads. Returns a
result dict compatible with the notebook's sequential `interval_coloring`, so the
drawing/logging cells work unchanged.
"""
from z3 import Int, Solver, Distinct, sat
from concurrent.futures import ProcessPoolExecutor, as_completed
import os


def _check_k(args):
    """Solve the interval-colouring feasibility for a single k. Runs in a child
    process, so it takes only picklable inputs (edge strings + index map) and
    returns a plain dict for the model (Z3 objects are not picklable)."""
    edges, inc, k, timeout_ms = args
    xs = [Int('e%d' % i) for i in range(len(edges))]
    s = Solver()
    if timeout_ms:                       # 0 -> no timeout (run to a definitive answer)
        s.set('timeout', timeout_ms)
    for x in xs:
        s.add(x >= 0, x <= k - 1)
    for v, es in inc.items():
        vs = [xs[i] for i in es]
        s.add(Distinct(vs))              # condition 1: incident edges all different
        if len(vs) > 1:
            lo = Int('lo_%s' % v)        # condition 2: consecutive window per vertex
            for x in vs:
                s.add(x >= lo, x <= lo + len(vs) - 1)
    r = s.check()
    if r == sat:
        m = s.model()
        sol = {edges[i]: m[xs[i]].as_long() for i in range(len(edges))}
        return (k, 'sat', sol)
    return (k, str(r), None)             # 'unsat' or 'unknown' (timed out)


def parallel_interval_coloring(edges, inc, delta, max_colors=None,
                               timeout_ms=0, max_workers=None):
    """Check every k in [delta, max_colors] concurrently and return the SMALLEST
    k that is provably SAT.

    edges  : list of 'u v' strings (from the notebook's all_edges()).
    inc    : {vertex: [edge indices incident to it]}.
    delta  : max vertex degree (the hard lower bound on colours).
    timeout_ms : per-k Z3 timeout in ms; 0 = unlimited (definitive answers,
                 but a hard k can run a long time). With a timeout, a k that
                 doesn't finish comes back 'unknown' and is reported, not hidden.
    max_workers: process count. Defaults to cpu_count-2 so you leave cores free
                 (oversubscribing makes every solve slower and, under a wall-clock
                 timeout, can flip a would-be SAT into 'unknown').
    """
    if max_colors is None:
        max_colors = 2 * delta
    ks = list(range(delta, max_colors + 1))
    if max_workers is None:
        max_workers = max(1, min(len(ks), (os.cpu_count() or 2) - 2))

    jobs = [(edges, inc, k, timeout_ms) for k in ks]
    results = {}
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_check_k, j): j[2] for j in jobs}
        for f in as_completed(futs):
            k, status, sol = f.result()
            results[k] = (status, sol)
            print('  k=%2d -> %s' % (k, status), flush=True)

    tested = len(ks)
    status_map = {k: st for k, (st, _) in results.items()}
    sats = sorted(k for k, (st, _) in results.items() if st == 'sat')
    if sats:
        kbest = sats[0]
        undecided = sorted(k for k, (st, _) in results.items()
                           if st == 'unknown' and k < kbest)
        return {'solution': results[kbest][1], 'colorable': True, 'colours': kbest,
                'colours_tested': tested, 'delta': delta, 'max_colors': max_colors,
                'undecided_below': undecided, 'all_results': status_map}
    return {'solution': None, 'colorable': False, 'colours': None,
            'colours_tested': tested, 'delta': delta, 'max_colors': max_colors,
            'undecided_below': [], 'all_results': status_map}


def race_interval_coloring(edges, inc, delta, max_colors=None, max_workers=None):
    """Run all k concurrently and return the FIRST k found SAT, then KILL every
    still-running solve immediately (pool.terminate sends SIGTERM to the workers,
    so the wasted Z3 compute actually stops).

    FAST, but returns *a* valid colouring -- NOT necessarily the fewest colours,
    because the first k to FINISH is usually not the smallest k (more colours are
    easier to satisfy). Use parallel_interval_coloring() when you need the minimum.
    """
    from multiprocessing import Pool
    if max_colors is None:
        max_colors = 2 * delta
    ks = list(range(delta, max_colors + 1))
    if max_workers is None:
        max_workers = max(1, min(len(ks), (os.cpu_count() or 2) - 2))

    jobs = [(edges, inc, k, 0) for k in ks]   # timeout 0: a fast SAT ends the race
    seen = {}
    with Pool(processes=max_workers) as pool:
        try:
            for k, status, sol in pool.imap_unordered(_check_k, jobs):
                seen[k] = status
                print('  k=%2d -> %s' % (k, status), flush=True)
                if status == 'sat':
                    pool.terminate()          # stop all other solves right now
                    return {'solution': sol, 'colorable': True, 'colours': k,
                            'colours_tested': len(seen), 'delta': delta,
                            'max_colors': max_colors, 'all_results': seen,
                            'note': 'first-found (not guaranteed minimal)'}
        finally:
            pool.terminate()
    return {'solution': None, 'colorable': False, 'colours': None,
            'colours_tested': len(seen), 'delta': delta, 'max_colors': max_colors,
            'all_results': seen}


def min_colors_parallel(graph, max_colors=None, timeout_ms=0, max_workers=None):
    """Convenience wrapper that takes a pygraphviz graph directly and returns the
    PROVEN minimum-colour interval colouring (same result dict shape as the
    notebook's sequential interval_coloring, so downstream cells work unchanged).

    Edges/incidence/delta are extracted here in the main process; only picklable
    data (edge strings + index map) is sent to the worker processes. timeout_ms=0
    means every k runs to a definitive sat/unsat, so the minimum stays *proven*.
    """
    edges = ['%s %s' % (u, v) for u, v in graph.edges()]
    inc = {}
    for i, e in enumerate(edges):
        u, v = e.split(' ')
        inc.setdefault(u, []).append(i)
        inc.setdefault(v, []).append(i)
    delta = max(len(es) for es in inc.values())
    return parallel_interval_coloring(edges, inc, delta, max_colors=max_colors,
                                      timeout_ms=timeout_ms, max_workers=max_workers)
