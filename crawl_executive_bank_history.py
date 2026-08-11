#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2010-2023 非ST、非金融业沪深A股：董监高银行任职/关联历史爬虫。"""
import argparse, csv, json, logging, random, re, tempfile, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from crawl_shareholders import Client as HolderClient
from crawl_executives import Cninfo, pdf_text, BANK_ALIASES, ROLE_TERMS, clean

CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "https://static.cninfo.com.cn/"
CNINFO_STOCKS = "https://www.cninfo.com.cn/new/data/szse_stock.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/", "X-Requested-With": "XMLHttpRequest"}

A_PREFIX = ("000","001","002","003","300","301","600","601","603","605","688","689")
ST_RE = re.compile(r"(?:^|\*)ST|S\*ST|SST|PT|退", re.I)
FIN_NAME_RE = re.compile(r"银行|证券|保险|信托|期货|金融|金控|金租|租赁|消费金融|财务公司")
EXCLUDE_TITLE = ["摘要","英文版","社会责任报告","可持续发展报告","ESG","问询","审计报告","鉴证报告","内部控制"]

BANK_TERMS = ["银行","分行","支行","农村信用社","信用联社","农商行","农商银行","国家开发银行","农业发展银行","进出口银行","中国人民银行"]
EMPLOY_VERBS = ["曾任","历任","任职于","任职","就职于","就职","工作于","曾在","先后任","进入","加入","现任","兼任","目前任","担任"]
JOB_TERMS = [
    "董事长","副董事长","董事","监事长","监事","行长助理","分行行长","支行行长","支行长","行长","副行长",
    "总经理","副总经理","总监","副总监","主任","副主任","处长","副处长","科长","副科长","经理","副经理",
    "客户经理","信贷员","信贷专员","会计","出纳","职员","员工","干部","负责人","党委书记","党委副书记",
    "纪委书记","风险总监","首席风险官","首席财务官","首席信息官","审计师","分析师","交易员"
]

NAME_RE = re.compile(
    r"(?:^|\n|[。；;])\s*(?:\d{1,2}[、\.．]\s*)?([\u4e00-\u9fa5·]{2,8}?)(?:先生|女士|[：:,，]\s*(?:男|女)?)",
    re.M,
)
DATE_TOKEN = r"(?:19|20)\d{2}(?:\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|[./-]\d{1,2}(?:[./-]\d{1,2})?)?"
PERIOD_RE = re.compile(rf"(?P<s>{DATE_TOKEN})\s*(?:至|到|—|－|-|~|～)\s*(?P<e>{DATE_TOKEN}|至今|今|现在|目前)")
START_RE = re.compile(rf"(?P<s>{DATE_TOKEN})\s*(?:起|开始)?\s*(?:进入|加入|任职于|任职|就职于|就职|工作于|工作|在)?\s*$")


def write_csv(path: Path, rows):
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen:
                fields.append(k); seen.add(k)
    if not fields:
        fields = ["无数据"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def code6(v):
    m = re.search(r"(\d{6})", str(v or ""))
    return m.group(1) if m else ""


def is_hushen_a(code):
    return len(code) == 6 and code.startswith(A_PREFIX)


def is_st_name(name):
    return bool(ST_RE.search(str(name or "").replace(" ", "")))


def annual_universe(year):
    raw = HolderClient().date(f"{year}-12-31")
    d = {}
    for x in raw:
        c = code6(x.get("SECURITY_CODE") or x.get("SECUCODE"))
        if not is_hushen_a(c):
            continue
        name = str(x.get("SECURITY_NAME_ABBR") or "").strip()
        if c not in d or (not d[c] and name):
            d[c] = name
    return [{"报告年度":year,"证券代码":c,"证券简称":n} for c,n in sorted(d.items())]


def classify_finance_one(code, name, year):
    base = {"行业分类标准":"证监会行业分类标准","行业编码":"","行业门类":"","行业大类":"","行业中类":"","行业次类":"","行业变更日期":""}
    last = None
    for attempt in range(4):
        try:
            import akshare as ak
            df = ak.stock_industry_change_cninfo(symbol=code, start_date="19900101", end_date=f"{year}1231")
            if df is None or df.empty:
                raise RuntimeError("empty industry history")
            x = df.copy()
            if "分类标准" in x.columns:
                z = x[x["分类标准"].astype(str).str.contains("证监会", na=False)]
                if not z.empty:
                    x = z
            if "变更日期" in x.columns:
                x = x.sort_values("变更日期")
            row = x.iloc[-1]
            for k in ["行业编码","行业门类","行业大类","行业中类","行业次类","变更日期"]:
                if k in row.index:
                    outk = "行业变更日期" if k == "变更日期" else k
                    base[outk] = "" if str(row.get(k)) == "nan" else str(row.get(k) or "")
            text = "|".join(str(base.get(k) or "") for k in ["行业编码","行业门类","行业大类","行业中类","行业次类"])
            codeval = str(base.get("行业编码") or "").upper()
            finance = int(codeval.startswith("J") or "金融业" in text or any(t in text for t in ["货币金融","资本市场服务","保险业","其他金融业"]))
            base.update({"是否金融业":finance,"行业识别状态":"巨潮历史行业成功","行业识别错误":""})
            return base
        except Exception as e:
            last = e
            time.sleep(min(8, 1.2 * (2 ** attempt)) + random.random())
    finance = int(bool(FIN_NAME_RE.search(name or "")))
    base.update({"是否金融业":finance,"行业识别状态":"巨潮失败_名称兜底","行业识别错误":repr(last)[:500]})
    return base


def add_industry(rows, year, workers=4):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut = {ex.submit(classify_finance_one, r["证券代码"], r["证券简称"], year): r for r in rows}
        done = 0
        for f in as_completed(fut):
            r = dict(fut[f]); r.update(f.result()); out.append(r); done += 1
            if done % 100 == 0:
                logging.info("industry %s %d/%d", year, done, len(rows))
    return sorted(out, key=lambda r:r["证券代码"])


class PerStockCninfo(Cninfo):
    def stock_map(self):
        last = None
        for i in range(5):
            try:
                r = self.s.get(CNINFO_STOCKS, timeout=45); r.raise_for_status(); j = r.json()
                return {str(x.get("code") or ""):str(x.get("orgId") or "") for x in j.get("stockList",[]) if x.get("code")}
            except Exception as e:
                last=e; time.sleep(min(10,1.5*(2**i))+random.random())
        raise RuntimeError(last)

    def _query(self, year, column, stock_expr):
        out=[]; page=1
        while True:
            data={
                "pageNum":page,"pageSize":30,"column":column,"tabName":"fulltext","plate":"","stock":stock_expr,
                "searchkey":"","secid":"","category":"category_ndbg_szsh;","trade":"",
                "seDate":f"{year+1}-01-01~{year+2}-08-31","sortName":"","sortType":"","isHLtitle":"true"
            }
            j=self.post(data); anns=j.get("announcements") or []
            if not anns: break
            for a in anns:
                title=re.sub(r"<.*?>","",str(a.get("announcementTitle") or ""))
                ct=clean(title)
                if f"{year}年年度报告" not in ct and f"{year}年度报告" not in ct: continue
                if any(x in title for x in EXCLUDE_TITLE): continue
                adjunct=str(a.get("adjunctUrl") or "")
                if not adjunct.lower().endswith(".pdf"): continue
                out.append({
                    "报告年度":year,"证券代码":str(a.get("secCode") or ""),"证券简称":str(a.get("secName") or ""),
                    "年报标题":title,"公告时间":int(a.get("announcementTime") or 0),"公告ID":str(a.get("announcementId") or ""),
                    "数据来源链接":CNINFO_PDF+adjunct.lstrip("/"),"交易所查询列":column
                })
            total=int(j.get("totalpages") or j.get("totalPages") or 0)
            if j.get("hasMore") is False or (total and page>=total) or len(anns)<30: break
            page += 1; time.sleep(random.uniform(.12,.30))
        return out

    def reports_for_universe(self, year, companies, batch_size=10):
        mp=self.stock_map(); found=[]; errors=[]
        for col in ["szse","sse"]:
            subset=[r for r in companies if ((r["证券代码"].startswith("6") and col=="sse") or (not r["证券代码"].startswith("6") and col=="szse"))]
            for i in range(0,len(subset),batch_size):
                batch=subset[i:i+batch_size]
                expr=[]
                for r in batch:
                    c=r["证券代码"]; org=mp.get(c) or (f"gssh0{c}" if c.startswith("6") else f"gssz0{c}")
                    expr.append(f"{c},{org}")
                try:
                    found.extend(self._query(year,col,";".join(expr)))
                except Exception as e:
                    errors.append({"报告年度":year,"阶段":"批量年报查询","证券代码":";".join(x["证券代码"] for x in batch),"错误":repr(e)})
                if (i//batch_size+1)%20==0:
                    logging.info("reports %s %s batch %d/%d found=%d",year,col,i//batch_size+1,(len(subset)+batch_size-1)//batch_size,len(found))
                time.sleep(random.uniform(.08,.20))
        best={}
        for r in found:
            k=r["证券代码"]
            old=best.get(k)
            score=(int("修订" in r["年报标题"] or "更新" in r["年报标题"]), int("更正" in r["年报标题"]), r["公告时间"])
            if old is None:
                best[k]=r
            else:
                oscore=(int("修订" in old["年报标题"] or "更新" in old["年报标题"]), int("更正" in old["年报标题"]), old["公告时间"])
                if score>oscore: best[k]=r
        missing=[r for r in companies if r["证券代码"] not in best]
        logging.info("%s initial reports=%d missing=%d",year,len(best),len(missing))
        for idx,r in enumerate(missing,1):
            c=r["证券代码"]; col="sse" if c.startswith("6") else "szse"
            try:
                rr=self._query(year,col,c)
                for x in rr:
                    if x["证券代码"]==c:
                        old=best.get(c)
                        if old is None or x["公告时间"]>old["公告时间"]: best[c]=x
            except Exception as e:
                errors.append({"报告年度":year,"阶段":"单股年报补查","证券代码":c,"证券简称":r["证券简称"],"错误":repr(e)})
            if idx%100==0: logging.info("report fallback %s %d/%d",year,idx,len(missing))
            time.sleep(random.uniform(.10,.25))
        return sorted(best.values(),key=lambda r:r["证券代码"]), errors


def normalize_date_token(x):
    x=re.sub(r"\s+","",str(x or ""))
    if x in {"今","至今","现在","目前"}: return "至今", "至今"
    m=re.match(r"((?:19|20)\d{2})(?:年|[./-])?(\d{1,2})?(?:月|[./-])?(\d{1,2})?",x)
    if not m: return x, "未知"
    y,mo,da=m.group(1),m.group(2),m.group(3)
    if da: return f"{y}-{int(mo):02d}-{int(da):02d}", "日"
    if mo: return f"{y}-{int(mo):02d}", "月"
    return y, "年"


def standard_bank_name(raw):
    t=clean(raw)
    if "投资银行" in t and not any(a in t for vs in BANK_ALIASES.values() for a in vs):
        return ""
    for std, aliases in BANK_ALIASES.items():
        for a in sorted(aliases,key=len,reverse=True):
            if clean(a) in t: return std
    t=re.sub(rf"^.*?(?:{DATE_TOKEN})(?:至|到|—|－|-|~|～)?(?:{DATE_TOKEN})?", "", t)
    for marker in ["先后任","历任","曾任","现任","目前任","兼任","曾在","进入","加入","任职于","就职于","工作于"]:
        if marker in t: t=t.rsplit(marker,1)[-1]
    t=re.sub(r"^(?:于|在|任)","",t)
    if len(t)>35: return ""
    m=re.search(r"^(.+?(?:农村商业银行|农村合作银行|商业银行|银行|农商行|农村信用社|信用联社)(?:股份有限公司|有限责任公司)?)(?:[\u4e00-\u9fa5]{0,10}(?:分行|支行))?$",t)
    if not m:
        return ""
    return m.group(1)


def bank_mentions(compact):
    out=[]
    for std,aliases in BANK_ALIASES.items():
        spans=[]
        for a in sorted(aliases,key=len,reverse=True):
            aa=clean(a); pos=0
            while True:
                p=compact.find(aa,pos)
                if p<0: break
                q=p+len(aa)
                if not any(not (q<=x or p>=y) for x,y in spans):
                    spans.append((p,q)); out.append((p,q,std,aa))
                pos=p+max(1,len(aa))
    pat=re.compile(r"[\u4e00-\u9fa5A-Za-z0-9·（）()]{2,45}(?:农村商业银行|农村合作银行|商业银行|银行|农商行|农村信用社|信用联社)(?:股份有限公司|有限责任公司)?(?:[\u4e00-\u9fa5]{0,10}(?:分行|支行))?")
    for m in pat.finditer(compact):
        raw=m.group(0); std=standard_bank_name(raw)
        if not std: continue
        if any(not (m.end()<=a or m.start()>=b) for a,b,_,_ in out): continue
        out.append((m.start(),m.end(),std,raw))
    return sorted(out,key=lambda x:x[0])


def closest_period(compact, pos):
    left=compact[max(0,pos-110):pos]
    right=compact[pos:min(len(compact),pos+180)]
    window=left+right
    offset=max(0,pos-110)
    best=None
    for m in PERIOD_RE.finditer(window):
        abs_end=offset+m.end()
        dist=abs(abs_end-pos)
        if dist<=150 and (best is None or dist<best[0]): best=(dist,m)
    if best:
        m=best[1]; s,sp=normalize_date_token(m.group("s")); e,ep=normalize_date_token(m.group("e"))
        precision="日" if "日" in (sp,ep) else ("月" if "月" in (sp,ep) else "年")
        return s,e,precision,m.group(0)
    lm=list(re.finditer(DATE_TOKEN,left))
    if lm:
        m=lm[-1]
        if pos-(max(0,pos-110)+m.end())<=55:
            s,sp=normalize_date_token(m.group(0))
            if "至今" in right[:100] or "工作至今" in right[:120]:
                return s,"至今",sp,m.group(0)+"…至今"
            return s,"",sp,m.group(0)
    return "","","未知",""


def extract_role(compact, start, end):
    tail=compact[end:min(len(compact),end+140)]
    cut=len(tail)
    for sep in ["；",";","。"]:
        p=tail.find(sep)
        if p>=0: cut=min(cut,p)
    comma=min([p for p in [tail.find("，"),tail.find(",")] if p>=0], default=-1)
    if comma>=0 and any(t in tail[:comma] for t in JOB_TERMS):
        cut=min(cut,comma)
    tail=tail[:cut]
    head=compact[max(0,start-25):start]
    ctx=head+compact[start:end]+tail
    roles=[]
    for t in JOB_TERMS:
        if t in tail and t not in roles: roles.append(t)
    return "、".join(roles[:6]), ctx


def direct_bank_relation(ctx, bank):
    if not bank: return False
    if "投资银行" in ctx and any(x in ctx for x in ["证券","券商","美林集团","瑞士信贷集团"]): return False
    noise=["银行账户","银行存款","银行贷款","银行授信","开户银行","募集资金专户","开设独立的银行","银行结算"]
    if any(x in ctx for x in noise) and not any(v in ctx for v in EMPLOY_VERBS) and not any(j in ctx for j in JOB_TERMS): return False
    return any(v in ctx for v in EMPLOY_VERBS) or any(j in ctx for j in JOB_TERMS) or bool(PERIOD_RE.search(ctx))


def extract_employment_rows(text, meta):
    if not any(t in text for t in BANK_TERMS): return [],[]
    ms=list(NAME_RE.finditer(text)); detail=[]; review=[]
    for i,m in enumerate(ms):
        start=m.start(); end=ms[i+1].start() if i+1<len(ms) else min(len(text),start+5000)
        end=min(end,start+6000)
        bio=text[start:end]
        compact=re.sub(r"\s+","",bio)
        if not any(t in compact for t in BANK_TERMS): continue
        if not any(t in compact[:1500] for t in ROLE_TERMS): continue
        person=m.group(1)
        mentions=bank_mentions(compact)
        for a,b,bank,raw in mentions:
            role,ctx=extract_role(compact,a,b)
            if not direct_bank_relation(ctx,bank):
                continue
            st,ed,prec,period_raw=closest_period(compact,a)
            if ed and ed != "至今":
                current=0
            elif ed == "至今":
                current=1
            else:
                before=compact[max(0,a-35):a]
                current=int(any(x in before for x in ["现任","目前任","现兼任","兼任"]))
            hist=int(not current)
            reltype="当前银行任职/兼职" if current else "历史银行任职"
            evidence=re.sub(r"\s+"," ",bio).strip()
            row={
                "报告年度":meta["报告年度"],"证券代码":meta["证券代码"],"证券简称":meta["证券简称"],"高管姓名":person,
                "银行原始名称":raw,"标准化银行名称":bank,"关联类型":reltype,"任职开始时间":st,"任职结束时间":ed,
                "时间精度":prec,"任职时间原文":period_raw,"银行内职务":role,"是否当前银行联结":current,"是否历史银行任职":hist,
                "证据原文":evidence[:3200],"年报标题":meta["年报标题"],"公告时间":meta["公告时间"],"数据来源链接":meta["数据来源链接"],
                "定义口径":"报告期内在任董监高简历中披露的银行机构正式任职、董事/监事/兼职或历史任职经历"
            }
            uncertain=int((not st and not ed) or len(bank)>30 or not role)
            row["是否需人工复核"]=uncertain
            detail.append(row)
            if uncertain: review.append(row)
    best={}
    for r in detail:
        k=(r["证券代码"],r["报告年度"],r["高管姓名"],r["标准化银行名称"],r["任职开始时间"],r["任职结束时间"],r["银行内职务"])
        if k not in best or len(r["证据原文"])>len(best[k]["证据原文"]): best[k]=r
    detail=list(best.values())
    keys={tuple(r.get(k,"") for k in ["证券代码","报告年度","高管姓名","标准化银行名称","任职开始时间","任职结束时间","银行内职务"]) for r in detail}
    review=[r for r in review if tuple(r.get(k,"") for k in ["证券代码","报告年度","高管姓名","标准化银行名称","任职开始时间","任职结束时间","银行内职务"]) in keys]
    return detail,review


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--year",type=int,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--industry-workers",type=int,default=4)
    args=ap.parse_args(); year=args.year; out=args.output
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    out.mkdir(parents=True,exist_ok=True); errors=[]

    uni=annual_universe(year)
    for r in uni: r["是否ST/退市整理"] = int(is_st_name(r["证券简称"]))
    nonst=[r for r in uni if not r["是否ST/退市整理"]]
    logging.info("%s A-share=%d nonST=%d",year,len(uni),len(nonst))

    classified=add_industry(nonst,year,args.industry_workers)
    cls_by_code={r["证券代码"]:r for r in classified}
    sample=[]
    for r in uni:
        rr=dict(r)
        if rr["是否ST/退市整理"]:
            rr.update({"是否金融业":"","行业识别状态":"ST已先剔除","最终是否纳入样本":0,"剔除原因":"ST/退市整理"})
        else:
            rr.update({k:v for k,v in cls_by_code[rr["证券代码"]].items() if k not in rr})
            fin=int(rr.get("是否金融业") or 0)
            rr["最终是否纳入样本"]=0 if fin else 1
            rr["剔除原因"]="金融业" if fin else ""
        sample.append(rr)
    write_csv(out/"01_样本公司清单_剔除ST金融业.csv",sample)
    kept=[r for r in sample if int(r.get("最终是否纳入样本") or 0)==1]
    logging.info("%s kept=%d excluded_st=%d excluded_fin=%d",year,len(kept),sum(int(r["是否ST/退市整理"]) for r in sample),sum(int(r.get("是否金融业") or 0) for r in sample if not r["是否ST/退市整理"]))

    c=PerStockCninfo()
    reports,qerr=c.reports_for_universe(year,kept); errors.extend(qerr)
    write_csv(out/"02_年报清单.csv",reports)
    report_codes={r["证券代码"] for r in reports}

    details=[]; reviews=[]; parsed=set()
    with tempfile.TemporaryDirectory() as td:
        td=Path(td)
        for idx,meta in enumerate(reports,1):
            try:
                pdf=td/f"{meta['证券代码']}.pdf"; c.download(meta["数据来源链接"],pdf)
                text=pdf_text(pdf); d,rv=extract_employment_rows(text,meta); details.extend(d); reviews.extend(rv); parsed.add(meta["证券代码"])
                try: pdf.unlink()
                except: pass
                if idx%25==0: logging.info("%s pdf %d/%d detail=%d",year,idx,len(reports),len(details))
                time.sleep(random.uniform(.08,.20))
            except Exception as e:
                errors.append({"报告年度":year,"证券代码":meta["证券代码"],"证券简称":meta["证券简称"],"阶段":"PDF下载/解析","错误":repr(e),"数据来源链接":meta["数据来源链接"]})
    details=sorted(details,key=lambda r:(r["证券代码"],r["高管姓名"],r["标准化银行名称"],r["任职开始时间"]))
    write_csv(out/"03_高管银行任职分段明细.csv",details)
    write_csv(out/"04_人工复核队列.csv",reviews)

    g=defaultdict(list)
    for r in details: g[r["证券代码"]].append(r)
    panel=[]
    for r in kept:
        code=r["证券代码"]; rs=g.get(code,[])
        covered=int(code in parsed)
        if rs: flag=1
        elif covered: flag=0
        else: flag=""
        panel.append({
            "报告年度":year,"证券代码":code,"证券简称":r["证券简称"],"年报是否找到":int(code in report_codes),"年报是否成功解析":covered,
            "是否存在高管银行关联":flag,
            "银行背景高管人数":len({x["高管姓名"] for x in rs}) if covered else "",
            "关联银行数量":len({x["标准化银行名称"] for x in rs}) if covered else "",
            "是否存在历史银行任职":int(any(int(x["是否历史银行任职"]) for x in rs)) if covered else "",
            "是否存在当前银行联结":int(any(int(x["是否当前银行联结"]) for x in rs)) if covered else "",
            "银行背景高管名单":"；".join(sorted({x["高管姓名"] for x in rs})),
            "关联银行列表":"；".join(sorted({x["标准化银行名称"] for x in rs})),
            "样本口径":"沪深A股；按当年年末简称剔除ST/退市整理；按当年证监会历史行业分类剔除金融业"
        })
    write_csv(out/"05_公司年度高管银行背景面板.csv",panel)
    write_csv(out/"99_错误日志.csv",errors)

    summary={
        "报告年度":year,"沪深A股公司数":len(uni),"剔除ST/退市整理公司数":sum(int(r["是否ST/退市整理"]) for r in sample),
        "剔除金融业公司数":sum(int(r.get("是否金融业") or 0) for r in sample if not r["是否ST/退市整理"]),"最终样本公司数":len(kept),
        "找到年报公司数":len(report_codes),"成功解析年报公司数":len(parsed),"银行任职分段记录数":len(details),
        "有银行背景公司数":len({r["证券代码"] for r in details}),"人工复核记录数":len(reviews),"错误记录数":len(errors),
        "说明":"任职开始/结束时间只提取年报简历明确披露的信息；未披露不推断。"
    }
    (out/"00_运行摘要.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
