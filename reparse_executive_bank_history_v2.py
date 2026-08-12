#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second-pass high-precision parser for executive bank histories.

Reads the repaired annual-report lists, re-downloads those PDFs, and reparses
with much stricter person boundaries and bank-employment evidence. This pass is
intentionally conservative: false positives are preferred to be dropped and
sent to review rather than coded as bank background.
"""
import argparse, csv, json, logging, re, tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import crawl_executive_bank_history as base
from crawl_executives import BANK_ALIASES, pdf_text

# A person biography must have a real biography-style boundary. A comma by
# itself is NOT enough (the prior rule matched ordinary prose such as
# "公司财务独立，...").
PERSON_START_RE = re.compile(
    r"(?:^|\n|[。；;])\s*(?:\d{1,2}[、\.．]\s*)?([\u4e00-\u9fa5·]{2,7}?)(?:"
    r"先生|女士|[：:]\s*(?:男|女)?|[，,]\s*(?:男|女)(?=[，,:：。；;\s]))",
    re.M,
)
PERSON_VALID_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$|^[\u4e00-\u9fa5·]{3,7}$")
PERSON_BAD = ["公司","报告","年度","期内","单位","会议","管理","情况","方面","合计","财务","独立","银行","董事","监事","经理","主任","本期","期末","截至","项目"]

BANK_ROLES = [
    "分行行长","支行行长","支行长","行长助理","副行长","行长",
    "副总经理","总经理","副总监","总监","副主任","主任","副处长","处长","副科长","科长",
    "客户经理","信贷员","信贷专员","职员","员工","负责人","党委副书记","党委书记","纪委书记",
    "风险总监","首席风险官","首席财务官","首席信息官","审计师","交易员","副经理","经理","会计","出纳",
    "独立董事","非执行董事","执行董事","董事长","副董事长","董事","监事长","监事"
]
STRONG_EMPLOY = ["曾任","历任","任职于","就职于","工作于","曾在","先后任","进入","加入"]
CURRENT_WORDS = ["现任","目前任","现兼任","兼任"]
NOISE = [
    "银行账户","银行帐号","银行存款","银行贷款","银行借款","银行授信","授信额度","银行理财","理财产品",
    "开户银行","募集资金","超募资金","银行承兑","向银行","申请银行","偿还银行","归还银行","融资额度",
    "投资银行部","投资银行业务","证券公司投资银行","证券业协会投资银行","公司独立在银行","拥有独立银行",
    "充分尊重和维护银行","尊重和维护银行","债权银行","非银行金融机构"
]
BANK_NAME_BAD = ["公司","充分","尊重","维护","独立","财务","相关","债权","非银行","拥有","开户","高校","师资","课程","证券业","金融及","劳动模范","优秀银行"]

EXTRA = {
    "中国人民银行":["中国人民银行","人民银行"],
    "世界银行":["世界银行"], "亚洲开发银行":["亚洲开发银行"],
    "汇丰银行":["汇丰银行","香港上海汇丰银行"], "渣打银行":["渣打银行"], "花旗银行":["花旗银行"],
    "德意志银行":["德意志银行"], "法国巴黎银行":["法国巴黎银行"], "东亚银行":["东亚银行"],
    "中国投资银行":["中国投资银行"], "东京银行":["东京银行"], "三和银行":["三和银行","日本三和银行"],
}
ALL_ALIASES={k:list(v) for k,v in BANK_ALIASES.items()}
for k,v in EXTRA.items(): ALL_ALIASES.setdefault(k,[]).extend(v)
ALIAS_TO_STD={}
for std,vals in ALL_ALIASES.items():
    ALIAS_TO_STD[std]=std
    for a in vals: ALIAS_TO_STD[a]=std

GENERIC_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,14}(?:农村商业银行|农村合作银行|商业银行|村镇银行|农村信用社|信用联社)(?:股份有限公司|有限责任公司)?|"
    r"[\u4e00-\u9fa5]{2,10}银行(?:股份有限公司|有限责任公司))"
)


def read_csv(p):
    with Path(p).open("r",encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))

def write_csv(p,rows,fields=None):
    rows=list(rows); p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    if fields is None:
        fields=[]; seen=set()
        for r in rows:
            for k in r:
                if k not in seen: fields.append(k); seen.add(k)
    if not fields: fields=["无数据"]
    with p.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def clean(x): return re.sub(r"\s+","",str(x or ""))

def valid_person(x):
    x=clean(x)
    return bool(PERSON_VALID_RE.fullmatch(x)) and not any(t in x for t in PERSON_BAD)

def normalize_bank(raw):
    t=clean(raw)
    for alias,std in sorted(ALIAS_TO_STD.items(), key=lambda kv:len(kv[0]), reverse=True):
        if alias in t: return std
    # Search the shortest plausible generic bank suffix inside the raw span.
    cands=[]
    for m in GENERIC_RE.finditer(t):
        x=m.group(1)
        if any(b in x for b in BANK_NAME_BAD): continue
        if len(x)>28: continue
        cands.append(x)
    if not cands: return ""
    # Prefer names with legal suffix; otherwise shortest plausible proper name.
    cands.sort(key=lambda x:(0 if ("股份有限公司" in x or "有限责任公司" in x) else 1, len(x)))
    return cands[0]

def bank_occurrences(text):
    out=[]; spans=[]
    for alias,std in sorted(ALIAS_TO_STD.items(), key=lambda kv:len(kv[0]), reverse=True):
        pos=0
        while True:
            i=text.find(alias,pos)
            if i<0: break
            j=i+len(alias)
            if not any(not (j<=a or i>=b) for a,b,_,_ in spans):
                spans.append((i,j,std,alias)); out.append((i,j,std,alias))
            pos=i+max(1,len(alias))
    for m in GENERIC_RE.finditer(text):
        raw=m.group(1); std=normalize_bank(raw)
        if not std or any(b in std for b in BANK_NAME_BAD): continue
        if any(not (m.end()<=a or m.start()>=b) for a,b,_,_ in spans): continue
        out.append((m.start(),m.end(),std,raw)); spans.append((m.start(),m.end(),std,raw))
    return sorted(out)

def local_sentence(text,a,b,limit=150):
    l=max(0,a-limit); r=min(len(text),b+limit)
    p=max(text.rfind("。",l,a),text.rfind("；",l,a),text.rfind(";",l,a))
    if p>=0: l=p+1
    qs=[q for q in [text.find("。",b,r),text.find("；",b,r),text.find(";",b,r)] if q>=0]
    if qs: r=min(qs)
    return text[l:r],a-l,b-l

def roles_near(seg,a,b):
    t=seg.replace("董事会","___").replace("监事会","___")
    left=t[max(0,a-22):a]; right=t[b:min(len(t),b+75)]
    roles=[]
    for role in BANK_ROLES:
        if role in right[:55] or role in left[-15:]:
            if role not in roles: roles.append(role)
    return "、".join(roles[:6])

def relation_ok(seg,a,b,person):
    local=seg[max(0,a-65):min(len(seg),b+85)]
    left=seg[max(0,a-60):a]
    role=roles_near(seg,a,b)
    strong=any(v in left[-45:] or v in local for v in STRONG_EMPLOY)
    current=any(v in left[-45:] for v in CURRENT_WORDS)
    # Generic "担任/任" only counts when a bank role is actually adjacent.
    weak=("担任" in left[-35:] or "任" in left[-20:] or current)
    noisy=any(n in local for n in NOISE)
    if noisy and not role: return False
    if role and (strong or weak or person in local): return True
    if strong and not any(x in local for x in ["培训","评估专家","顾问机构","合作项目","课题"]): return True
    return False

def period_near(text,a,b):
    l=max(0,a-75); r=min(len(text),b+90); w=text[l:r]
    best=None
    for m in base.PERIOD_RE.finditer(w):
        center=l+(m.start()+m.end())//2; d=min(abs(center-a),abs(center-b))
        if d<=70 and (best is None or d<best[0]): best=(d,m)
    if best:
        m=best[1]; s,sp=base.normalize_date_token(m.group("s")); e,ep=base.normalize_date_token(m.group("e"))
        prec="日" if "日" in (sp,ep) else ("月" if "月" in (sp,ep) else "年")
        return s,e,prec,m.group(0)
    return "","","未知",""

def parse_text(text,meta):
    ms=list(PERSON_START_RE.finditer(text)); rows=[]
    for i,m in enumerate(ms):
        person=clean(m.group(1))
        if not valid_person(person): continue
        start=m.start(); end=ms[i+1].start() if i+1<len(ms) else min(len(text),start+4200)
        end=min(end,start+4200)
        bio=text[start:end]; compact=clean(bio)
        if "银行" not in compact and "信用社" not in compact and "信用联社" not in compact: continue
        for a,b,bank,raw in bank_occurrences(compact):
            seg,sa,sb=local_sentence(compact,a,b)
            if not relation_ok(seg,sa,sb,person): continue
            role=roles_near(seg,sa,sb)
            st,ed,prec,praw=period_near(compact,a,b)
            before=seg[max(0,sa-50):sa]
            current=1 if (ed=="至今" or any(x in before for x in CURRENT_WORDS)) else 0
            evidence=re.sub(r"\s+"," ",bio).strip()[:3200]
            rows.append({
                "报告年度":meta["报告年度"],"证券代码":meta["证券代码"],"证券简称":meta["证券简称"],"高管姓名":person,
                "银行原始名称":raw,"标准化银行名称":bank,"关联类型":"当前银行任职/兼职" if current else "历史银行任职",
                "任职开始时间":st,"任职结束时间":ed,"时间精度":prec,"任职时间原文":praw,"银行内职务":role,
                "是否当前银行联结":current,"是否历史银行任职":0 if current else 1,"证据原文":evidence,
                "年报标题":meta["年报标题"],"公告时间":meta["公告时间"],"数据来源链接":meta["数据来源链接"],
                "定义口径":"报告期内在任董监高简历中明确披露的银行机构正式任职、董事/监事/兼职或历史任职经历（高精度保守口径）",
                "是否需人工复核":1 if (not st or not ed or not role) else 0,
                "V2清洗说明":"仅接受真实高管简历边界；银行名称局部必须有正式任职动词或相邻银行职务；排除账户/贷款/授信/理财/融资/投资银行/培训专家等噪声"
            })
    best={}
    for r in rows:
        k=(r["证券代码"],r["报告年度"],r["高管姓名"],r["标准化银行名称"],r["任职开始时间"],r["任职结束时间"],r["银行内职务"],r["关联类型"])
        if k not in best or len(r["证据原文"])>len(best[k]["证据原文"]): best[k]=r
    return list(best.values())

def process_report(meta,client,tmpdir):
    pdf=Path(tmpdir)/f"{meta['证券代码']}.pdf"
    try:
        client.download(meta["数据来源链接"],pdf); text=pdf_text(pdf); return meta["证券代码"],parse_text(text,meta),""
    except Exception as e:
        return meta["证券代码"],[],repr(e)[:1000]
    finally:
        try: pdf.unlink()
        except Exception: pass

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True); ap.add_argument("--input-dir",type=Path,required=True); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--pdf-workers",type=int,default=3)
    a=ap.parse_args(); year=a.year; a.output.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    sample=read_csv(a.input_dir/"01_样本公司清单_剔除ST金融业.csv")
    reports=read_csv(a.input_dir/"02_年报清单_修复后.csv")
    kept=[r for r in sample if str(r.get("最终是否纳入样本")) in {"1","1.0"}]
    client=base.PerStockCninfo(); details=[]; parsed=set(); errors=[]
    with tempfile.TemporaryDirectory() as td:
        with ThreadPoolExecutor(max_workers=a.pdf_workers) as ex:
            fut={ex.submit(process_report,r,client,td):r for r in reports}
            done=0
            for f in as_completed(fut):
                code,rows,err=f.result(); done+=1
                if err: errors.append({"报告年度":year,"阶段":"V2年报解析","证券代码":code,"错误":err})
                else: parsed.add(code); details.extend(rows)
                if done%100==0: logging.info("V2 %s pdf %d/%d detail=%d",year,done,len(reports),len(details))
    # Deduplicate again globally.
    best={}
    for r in details:
        k=(r["证券代码"],r["报告年度"],r["高管姓名"],r["标准化银行名称"],r["任职开始时间"],r["任职结束时间"],r["银行内职务"],r["关联类型"])
        if k not in best or len(r["证据原文"])>len(best[k]["证据原文"]): best[k]=r
    details=sorted(best.values(),key=lambda r:(r["证券代码"],r["高管姓名"],r["标准化银行名称"],r["任职开始时间"],r["任职结束时间"]))
    review=[r for r in details if int(r.get("是否需人工复核") or 0)==1]
    by=defaultdict(list)
    for r in details: by[r["证券代码"]].append(r)
    report_codes={r["证券代码"] for r in reports}
    panel=[]
    for s in kept:
        c=s["证券代码"]; rr=by.get(c,[]); found=int(c in report_codes); ok=int(c in parsed)
        panel.append({"报告年度":year,"证券代码":c,"证券简称":s["证券简称"],"年报是否找到":found,"年报是否成功解析":ok,
            "是否存在高管银行关联":1 if rr else (0 if ok else ""),"银行背景高管人数":len(set(x["高管姓名"] for x in rr)) if ok else "",
            "关联银行数量":len(set(x["标准化银行名称"] for x in rr)) if ok else "","是否存在历史银行任职":int(any(int(x["是否历史银行任职"]) for x in rr)) if ok else "",
            "是否存在当前银行联结":int(any(int(x["是否当前银行联结"]) for x in rr)) if ok else "","银行背景高管名单":"；".join(sorted(set(x["高管姓名"] for x in rr))),
            "关联银行列表":"；".join(sorted(set(x["标准化银行名称"] for x in rr))),"样本口径":"沪深A股；按当年年末简称剔除ST/退市整理；按当年证监会历史行业分类剔除金融业；高管银行背景采用V2高精度保守口径"})
    write_csv(a.output/"01_样本公司清单_剔除ST金融业.csv",sample)
    write_csv(a.output/"02_年报清单_V2.csv",reports)
    write_csv(a.output/"03_高管银行任职分段明细_V2高精度.csv",details)
    write_csv(a.output/"04_人工复核队列_V2.csv",review)
    write_csv(a.output/"05_公司年度高管银行背景面板_V2.csv",panel)
    write_csv(a.output/"99_错误日志_V2.csv",errors)
    summary={"报告年度":year,"最终样本公司数":len(kept),"年报覆盖数":len(report_codes),"年报覆盖率（%）":round(100*len(report_codes)/len(kept),2) if kept else 0,
        "成功解析年报公司数":len(parsed),"V2高精度银行任职记录数":len(details),"有银行背景公司数_V2":len(by),"人工复核记录数_V2":len(review),"错误记录数":len(errors),
        "说明":"V2重新下载修复后年报并以真实高管简历边界重解析；排除普通正文、账户/贷款/授信/理财/融资/投资银行/培训专家等噪声。起止时间仅保留局部明确披露。"}
    (a.output/"00_运行摘要_V2.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
