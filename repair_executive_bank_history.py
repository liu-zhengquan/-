#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair 2010-2023 executive-bank-history artifacts.

Repairs two QC issues from the first run:
1) Missing annual reports: query each missing company with the required `code,orgId` CNINFO stock expression.
2) False-positive bank histories: apply a conservative, evidence-local strict filter and recompute employment periods.
"""
import argparse, csv, json, logging, random, re, tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from crawl_executive_bank_history import (
    PerStockCninfo, extract_employment_rows, pdf_text, BANK_ALIASES,
    PERIOD_RE, DATE_TOKEN, normalize_date_token,
)

EMPLOY_VERBS = ["曾任","历任","任职于","任职","就职于","就职","工作于","工作","曾在","先后任","进入","加入","现任","目前任","兼任","现兼任","担任"]
BANK_ROLES = [
    "分行行长","支行行长","支行长","行长助理","副行长","行长",
    "副总经理","总经理","副总监","总监","副主任","主任","副处长","处长","副科长","科长",
    "客户经理","信贷员","信贷专员","职员","员工","负责人","党委副书记","党委书记","纪委书记",
    "风险总监","首席风险官","首席财务官","首席信息官","审计师","交易员","副经理","经理","会计","出纳","董事","监事"
]
NAME_BAD = [
    "公司","报告","董事","监事","经理","财务","年度","期内","如下","单位","会议","管理","方面","工程",
    "公告","集团","委员会","主任","秘书","中心","项目","管理员","本期","期末","万元","人民币","申请","使用",
    "审议","通过","独立","银行","情况","截至","合计","投资","本公司","报告期","事长","工程师","保驾护航"
]
BANK_NOISE = [
    "关于","公司独立","本公司","上市公司","年度","募集资金","超募资金","流动资金","偿还","归还","申请","使用",
    "独立在银行","独立开设银行","开设银行","拥有独立","公司在银行","公司银行","向银行","银行账户","银行帐号","银行存款",
    "银行贷款","银行授信","银行理财","理财产品","融资","担保","资金来源","逾期","获得但未使用","人民币类型","开户银行",
    "保证上市公司","财务方面","投资银行部","投资银行业务","证券业协会投资银行","内部银行","电子银行","个人银行"
]
LOCAL_FIN_NOISE = [
    "银行账户","银行帐号","银行存款","银行贷款","银行授信","银行理财","理财产品","向银行","申请银行","偿还银行","归还银行",
    "募集资金","超募资金","担保额度","融资额度","授信额度","开户银行","贷款银行","银行结算"
]

EXTRA_ALIASES = {
    "中国人民银行":["中国人民银行","人民银行"],
    "世界银行":["世界银行"], "亚洲开发银行":["亚洲开发银行","亚行"],
    "汇丰银行":["汇丰银行","香港上海汇丰银行"], "渣打银行":["渣打银行"], "花旗银行":["花旗银行"],
    "德意志银行":["德意志银行"], "法国巴黎银行":["法国巴黎银行"], "东亚银行":["东亚银行"],
    "中国投资银行":["中国投资银行"],
}
ALL_ALIASES = {k:list(v) for k,v in BANK_ALIASES.items()}
for k,v in EXTRA_ALIASES.items(): ALL_ALIASES.setdefault(k,[]).extend(v)
ALIAS_TO_STD = {}
for std, vals in ALL_ALIASES.items():
    ALIAS_TO_STD[std] = std
    for a in vals: ALIAS_TO_STD[a] = std
KNOWN_STD = set(ALL_ALIASES)

GENERIC_BANK_RE = re.compile(r"([\u4e00-\u9fa5]{2,24}(?:农村商业银行|农村合作银行|商业银行|村镇银行|银行)(?:股份有限公司|有限责任公司)?)")
VALID_GENERIC_RE = re.compile(r"^[\u4e00-\u9fa5]{2,24}(?:农村商业银行|农村合作银行|商业银行|村镇银行|银行)(?:股份有限公司|有限责任公司)?$")
PERSON_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$|^[\u4e00-\u9fa5·]{3,7}$")


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, preferred_fields=None):
    rows=list(rows); path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fields=[]; seen=set()
    if preferred_fields:
        for k in preferred_fields:
            if k not in seen: fields.append(k); seen.add(k)
    for r in rows:
        for k in r:
            if k not in seen: fields.append(k); seen.add(k)
    if not fields: fields=["无数据"]
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def valid_person(name):
    n=re.sub(r"\s+","",str(name or ""))
    if not PERSON_RE.fullmatch(n): return False
    if any(x in n for x in NAME_BAD): return False
    return True


def normalize_known_bank(text):
    t=re.sub(r"\s+","",str(text or ""))
    for alias,std in sorted(ALIAS_TO_STD.items(), key=lambda kv:len(kv[0]), reverse=True):
        if alias in t: return std
    return ""


def valid_generic_bank(x):
    x=re.sub(r"\s+","",str(x or ""))
    if not VALID_GENERIC_RE.fullmatch(x): return False
    if len(x)>30: return False
    if any(n in x for n in BANK_NOISE): return False
    if any(j in x for j in BANK_ROLES): return False
    if any(v in x for v in EMPLOY_VERBS): return False
    return True


def salvage_bank(row):
    evidence=re.sub(r"\s+","",str(row.get("证据原文") or ""))
    raw=re.sub(r"\s+","",str(row.get("银行原始名称") or ""))
    std=re.sub(r"\s+","",str(row.get("标准化银行名称") or ""))
    k=normalize_known_bank(raw+"|"+std)
    if k: return k
    if valid_generic_bank(std): return std
    cands=[]
    for m in GENERIC_BANK_RE.finditer(evidence):
        x=m.group(1); variants=[x]
        for i in range(1,min(14,len(x)-1)): variants.append(x[i:])
        good=[v for v in variants if valid_generic_bank(v)]
        if not good: continue
        v=min((g for g in good if len(g)>=4), key=len, default=min(good,key=len))
        score=int(v in raw or v in std or raw in v or std in v)
        cands.append((score,-abs(m.start()-max(0,evidence.find(raw))),-len(v),v))
    if cands:
        cands.sort(reverse=True); return cands[0][3]
    return ""


def bank_search_terms(bank):
    vals=[]
    if bank in ALL_ALIASES:
        vals.extend(ALL_ALIASES[bank]); vals.append(bank)
    else: vals.append(bank)
    return sorted(set(v for v in vals if v),key=len,reverse=True)


def occurrences(text, terms):
    out=[]
    for t in terms:
        pos=0
        while True:
            i=text.find(t,pos)
            if i<0: break
            out.append((i,i+len(t),t)); pos=i+max(1,len(t))
    return sorted(out)


def nearest_sentence(text,a,b,limit=180):
    l=max(0,a-limit); r=min(len(text),b+limit)
    for sep in ["。","；",";"]:
        p=text.rfind(sep,l,a)
        if p>=0: l=max(l,p+1)
        q=text.find(sep,b,r)
        if q>=0: r=min(r,q)
    return text[l:r], a-l, b-l


def extract_roles_local(seg, a, b):
    t=seg.replace("董事会","___").replace("监事会","___")
    left=t[max(0,a-25):a]; right=t[b:min(len(t),b+110)]
    roles=[]
    for role in BANK_ROLES:
        if role in right[:70] or role in left[-18:]:
            if role not in roles: roles.append(role)
    return "、".join(roles[:8])


def relation_score(seg,a,b,person):
    t=seg.replace("董事会","___").replace("监事会","___")
    left=t[max(0,a-65):a]; local=t[max(0,a-70):min(len(t),b+100)]
    verb=any(v in left[-50:] or v in local for v in EMPLOY_VERBS)
    role=bool(extract_roles_local(seg,a,b))
    person_near=person and (person in t[max(0,a-70):a+5])
    noisy=any(x in local for x in LOCAL_FIN_NOISE)
    if noisy and not verb and not (role and person_near): return 0
    if verb and role: return 4
    if verb: return 3
    if role and person_near: return 3
    if role and (b-a)>=4: return 2
    return 0


def strict_period(compact, abs_a, abs_b):
    l=max(0,abs_a-80); r=min(len(compact),abs_b+95); w=compact[l:r]
    best=None
    for m in PERIOD_RE.finditer(w):
        center=l+(m.start()+m.end())//2
        dist=min(abs(center-abs_a),abs(center-abs_b))
        if dist<=80 and (best is None or dist<best[0]): best=(dist,m)
    if best:
        m=best[1]; s,sp=normalize_date_token(m.group("s")); e,ep=normalize_date_token(m.group("e"))
        prec="日" if "日" in (sp,ep) else ("月" if "月" in (sp,ep) else "年")
        return s,e,prec,m.group(0)
    left=compact[max(0,abs_a-45):abs_a]; ms=list(re.finditer(DATE_TOKEN,left))
    if ms:
        m=ms[-1]
        if len(left)-m.end()<=22:
            s,sp=normalize_date_token(m.group(0)); return s,"",sp,m.group(0)
    return "","","未知",""


def strict_clean_row(row):
    person=re.sub(r"\s+","",str(row.get("高管姓名") or ""))
    if not valid_person(person): return None
    evidence=str(row.get("证据原文") or ""); compact=re.sub(r"\s+","",evidence)
    bank=salvage_bank(row)
    if not bank: return None
    if bank not in KNOWN_STD and not valid_generic_bank(bank): return None
    terms=bank_search_terms(bank); occ=occurrences(compact,terms)
    if not occ: return None
    best=None
    for a,b,term in occ:
        seg,sa,sb=nearest_sentence(compact,a,b); score=relation_score(seg,sa,sb,person)
        if score<=0: continue
        ppos=compact.rfind(person,max(0,a-900),a+1); pdist=a-ppos if ppos>=0 else 9999
        cand=(score,-pdist,-a,a,b,seg,sa,sb,term)
        if best is None or cand[:3]>best[:3]: best=cand
    if best is None: return None
    _,_,_,a,b,seg,sa,sb,term=best
    role=extract_roles_local(seg,sa,sb); st,ed,prec,praw=strict_period(compact,a,b)
    before=seg[max(0,sa-45):sa]
    current=1 if (ed=="至今" or any(x in before for x in ["现任","目前任","现兼任","兼任"])) else 0
    out=dict(row); out["高管姓名"]=person; out["标准化银行名称"]=bank
    out["关联类型"]="当前银行任职/兼职" if current else "历史银行任职"
    out["任职开始时间"]=st; out["任职结束时间"]=ed; out["时间精度"]=prec; out["任职时间原文"]=praw
    out["银行内职务"]=role; out["是否当前银行联结"]=current; out["是否历史银行任职"]=0 if current else 1
    out["是否需人工复核"]=1 if (not st or not ed or not role or bank not in KNOWN_STD) else 0
    out["严格清洗说明"]="银行机构名称有效，且银行名称局部上下文存在任职动词或相邻银行职务；排除账户/贷款/授信/理财/投资银行等正文噪声"
    return out


def dedupe_details(rows):
    best={}
    for r in rows:
        k=(r.get("报告年度"),r.get("证券代码"),r.get("高管姓名"),r.get("标准化银行名称"),r.get("任职开始时间"),r.get("任职结束时间"),r.get("银行内职务"),r.get("关联类型"))
        old=best.get(k)
        if old is None or len(str(r.get("证据原文") or ""))>len(str(old.get("证据原文") or "")): best[k]=r
    return sorted(best.values(),key=lambda r:(str(r.get("报告年度")),str(r.get("证券代码")),str(r.get("高管姓名")),str(r.get("标准化银行名称")),str(r.get("任职开始时间"))))


def query_missing_one(year,row,orgmap):
    code=row["证券代码"]; col="sse" if code.startswith("6") else "szse"
    org=orgmap.get(code) or (f"gssh0{code}" if code.startswith("6") else f"gssz0{code}")
    cli=PerStockCninfo(); rr=cli._query(year,col,f"{code},{org}")
    rr=[x for x in rr if x.get("证券代码")==code]
    if not rr: return None
    def score(x): return (int("修订" in x.get("年报标题","") or "更新" in x.get("年报标题","")),int("更正" in x.get("年报标题","")),int(x.get("公告时间") or 0))
    return max(rr,key=score)


def parse_report(meta,td):
    cli=PerStockCninfo(); code=meta["证券代码"]; pdf=Path(td)/f"{code}_{random.randint(1,10**9)}.pdf"
    cli.download(meta["数据来源链接"],pdf); text=pdf_text(pdf); raw,_=extract_employment_rows(text,meta)
    try: pdf.unlink()
    except: pass
    return raw


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True); ap.add_argument("--input-dir",type=Path,required=True); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--query-workers",type=int,default=4); ap.add_argument("--pdf-workers",type=int,default=3)
    args=ap.parse_args(); year=args.year; inp=args.input_dir; out=args.output; out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")

    sample=read_csv(inp/"01_样本公司清单_剔除ST金融业.csv"); reports=read_csv(inp/"02_年报清单.csv"); raw_old=read_csv(inp/"03_高管银行任职分段明细.csv")
    old_errors=read_csv(inp/"99_错误日志.csv") if (inp/"99_错误日志.csv").exists() else []
    old_errors=[] if (old_errors and list(old_errors[0].keys())==["无数据"]) else old_errors
    kept=[r for r in sample if str(r.get("最终是否纳入样本"))=="1"]
    bycode={r["证券代码"]:r for r in reports}; missing=[r for r in kept if r["证券代码"] not in bycode]
    logging.info("%s kept=%d prior_reports=%d missing=%d",year,len(kept),len(reports),len(missing))

    errors=list(old_errors); c=PerStockCninfo(); orgmap=c.stock_map(); new_reports=[]
    with ThreadPoolExecutor(max_workers=args.query_workers) as ex:
        fut={ex.submit(query_missing_one,year,r,orgmap):r for r in missing}; done=0
        for f in as_completed(fut):
            r=fut[f]; done+=1
            try:
                x=f.result()
                if x: new_reports.append(x)
            except Exception as e:
                errors.append({"报告年度":year,"证券代码":r["证券代码"],"证券简称":r.get("证券简称",""),"阶段":"修复_单股年报查询","错误":repr(e)})
            if done%100==0: logging.info("%s repair query %d/%d new=%d",year,done,len(missing),len(new_reports))
    for r in new_reports: bycode[r["证券代码"]]=r
    reports=sorted(bycode.values(),key=lambda r:r["证券代码"])
    logging.info("%s repaired reports=%d coverage=%.2f%%",year,len(reports),100*len(reports)/max(1,len(kept)))

    raw_new=[]; parsed_new=set()
    with tempfile.TemporaryDirectory() as td:
        with ThreadPoolExecutor(max_workers=args.pdf_workers) as ex:
            fut={ex.submit(parse_report,m,td):m for m in new_reports}; done=0
            for f in as_completed(fut):
                m=fut[f]; done+=1
                try:
                    rr=f.result(); raw_new.extend(rr); parsed_new.add(m["证券代码"])
                except Exception as e:
                    errors.append({"报告年度":year,"证券代码":m["证券代码"],"证券简称":m.get("证券简称",""),"阶段":"修复_PDF解析","错误":repr(e),"数据来源链接":m.get("数据来源链接","")})
                if done%25==0: logging.info("%s repair pdf %d/%d raw_new=%d",year,done,len(new_reports),len(raw_new))

    raw_all=raw_old+raw_new; strict=[]; dropped=0
    for r in raw_all:
        x=strict_clean_row(r)
        if x: strict.append(x)
        else: dropped+=1
    strict=dedupe_details(strict); reviews=[r for r in strict if str(r.get("是否需人工复核"))=="1"]
    logging.info("%s strict=%d dropped=%d review=%d",year,len(strict),dropped,len(reviews))

    parsed_codes={r["证券代码"] for r in read_csv(inp/"02_年报清单.csv")} | parsed_new
    g=defaultdict(list)
    for r in strict: g[r["证券代码"]].append(r)
    sample_by={r["证券代码"]:r for r in kept}; panel=[]
    for code in sorted(sample_by):
        base=sample_by[code]; rs=g.get(code,[]); found=code in bycode; parsed=code in parsed_codes
        if found and parsed:
            rel=1 if rs else 0
            panel.append({
                "报告年度":year,"证券代码":code,"证券简称":base.get("证券简称",""),"年报是否找到":1,"年报是否成功解析":1,
                "是否存在高管银行关联":rel,"银行背景高管人数":len({x["高管姓名"] for x in rs}) if rs else 0,
                "关联银行数量":len({x["标准化银行名称"] for x in rs}) if rs else 0,
                "是否存在历史银行任职":1 if any(str(x.get("是否历史银行任职"))=="1" for x in rs) else 0,
                "是否存在当前银行联结":1 if any(str(x.get("是否当前银行联结"))=="1" for x in rs) else 0,
                "银行背景高管名单":"；".join(sorted({x["高管姓名"] for x in rs})),"关联银行列表":"；".join(sorted({x["标准化银行名称"] for x in rs})),
                "样本口径":"沪深A股；按当年年末简称剔除ST/退市整理；按当年证监会历史行业分类剔除金融业；高管银行背景采用严格局部证据口径"
            })
        else:
            panel.append({"报告年度":year,"证券代码":code,"证券简称":base.get("证券简称",""),"年报是否找到":1 if found else 0,"年报是否成功解析":1 if parsed else 0,"是否存在高管银行关联":"","银行背景高管人数":"","关联银行数量":"","是否存在历史银行任职":"","是否存在当前银行联结":"","银行背景高管名单":"","关联银行列表":"","样本口径":"沪深A股；按当年年末简称剔除ST/退市整理；按当年证监会历史行业分类剔除金融业；未覆盖公司不编码为0"})

    write_csv(out/"01_样本公司清单_剔除ST金融业.csv",sample); write_csv(out/"02_年报清单_修复后.csv",reports); write_csv(out/"03A_高管银行候选_清洗前.csv",raw_all)
    write_csv(out/"03_高管银行任职分段明细_严格.csv",strict); write_csv(out/"04_人工复核队列_严格.csv",reviews); write_csv(out/"05_公司年度高管银行背景面板_严格.csv",panel); write_csv(out/"99_错误日志.csv",errors)

    found=len(reports); parsed=len(parsed_codes & set(sample_by)); relcos=len({r["证券代码"] for r in strict})
    summary={
        "报告年度":year,"最终样本公司数":len(kept),"修复前年报覆盖数":len(read_csv(inp/"02_年报清单.csv")),"修复后年报覆盖数":found,
        "修复后年报覆盖率（%）":round(100*found/max(1,len(kept)),2),"成功解析年报公司数":parsed,
        "原始银行候选记录数":len(raw_all),"严格清洗后银行任职记录数":len(strict),"严格清洗剔除记录数":dropped,
        "有银行背景公司数_严格":relcos,"人工复核记录数_严格":len(reviews),"错误记录数":len(errors),
        "说明":"修复缺失年报时使用证券代码+巨潮orgId逐股查询；银行背景采用严格局部证据过滤。任职起止时间仅保留年报简历局部明确披露，不推断。"
    }
    (out/"00_运行摘要_修复后.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
