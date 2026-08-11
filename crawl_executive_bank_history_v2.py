#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Coverage fix for crawl_executive_bank_history.py.

The v1 crawler batched CNINFO's `stock` parameter with semicolon-separated
`code,orgId` pairs, then used bare security codes for fallback lookups. CNINFO's
announcement query expects the stock selector in `code,orgId` form, which caused
large silent under-coverage despite successful jobs.

This wrapper keeps the validated sample/industry/bank-history extraction logic,
but replaces annual-report discovery with independent per-stock queries using
an explicit `code,orgId` selector and parallel retrieval.
"""
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import crawl_executive_bank_history as old

OriginalPerStockCninfo = old.PerStockCninfo


class FixedPerStockCninfo(OriginalPerStockCninfo):
    def reports_for_universe(self, year, companies, batch_size=10):
        mp = self.stock_map()
        errors = []
        workers = max(1, int(os.environ.get("REPORT_WORKERS", "8")))

        def score(r):
            return (
                int("修订" in r["年报标题"] or "更新" in r["年报标题"]),
                int("更正" in r["年报标题"]),
                int(r.get("公告时间") or 0),
            )

        def fetch_one(company):
            c = company["证券代码"]
            col = "sse" if c.startswith("6") else "szse"
            org = mp.get(c) or (f"gssh0{c}" if c.startswith("6") else f"gssz0{c}")
            client = OriginalPerStockCninfo()
            attempts = [f"{c},{org}"]
            # A small fallback set for historical/delisted securities whose
            # current stock map may no longer expose the old orgId.
            fallback_org = f"gssh0{c}" if c.startswith("6") else f"gssz0{c}"
            if fallback_org != org:
                attempts.append(f"{c},{fallback_org}")
            attempts.append(c)
            seen = []
            last = None
            for selector in attempts:
                try:
                    rr = client._query(year, col, selector)
                    rr = [x for x in rr if x.get("证券代码") == c]
                    if rr:
                        seen.extend(rr)
                        break
                except Exception as e:
                    last = e
                time.sleep(random.uniform(0.05, 0.12))
            if not seen and last is not None:
                return c, None, {
                    "报告年度": year,
                    "阶段": "单股年报精确查询",
                    "证券代码": c,
                    "证券简称": company.get("证券简称", ""),
                    "错误": repr(last),
                }
            if not seen:
                return c, None, None
            return c, max(seen, key=score), None

        best = {}
        total = len(companies)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_one, r): r for r in companies}
            done = 0
            for f in as_completed(futs):
                done += 1
                try:
                    c, row, err = f.result()
                    if row is not None:
                        best[c] = row
                    if err is not None:
                        errors.append(err)
                except Exception as e:
                    r = futs[f]
                    errors.append({
                        "报告年度": year,
                        "阶段": "单股年报精确查询线程",
                        "证券代码": r.get("证券代码", ""),
                        "证券简称": r.get("证券简称", ""),
                        "错误": repr(e),
                    })
                if done % 100 == 0:
                    old.logging.info(
                        "precise reports %s %d/%d found=%d errors=%d",
                        year, done, total, len(best), len(errors)
                    )
        old.logging.info(
            "%s precise report coverage=%d/%d (%.2f%%)",
            year, len(best), total, (100.0 * len(best) / total if total else 0.0)
        )
        return sorted(best.values(), key=lambda r: r["证券代码"]), errors


old.PerStockCninfo = FixedPerStockCninfo

if __name__ == "__main__":
    old.main()
