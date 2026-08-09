#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, json, logging, random, re, time
from collections import defaultdict
from pathlib import Path
import requests

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

BANK_ALIASES = {
    "工商银行": ["中国工商银行股份有限公司", "中国工商银行", "工商银行"],
    "建设银行": ["中国建设银行股份有限公司", "中国建设银行", "建设银行"],
    "农业银行": ["中国农业银行股份有限公司", "中国农业银行", "农业银行"],
    "中国银行": ["中国银行股份有限公司", "中国银行"],
    "交通银行": ["交通银行股份有限公司", "交通银行"],
    "邮储银行": ["中国邮政储蓄银行股份有限公司", "中国邮政储蓄银行", "邮储银行"],
    "招商银行": ["招商银行股份有限公司", "招商银行"],
    "浦发银行": ["上海浦东发展银行股份有限公司", "浦发银行"],
    "中信银行": ["中信银行股份有限公司", "中信银行"],
    "兴业银行": ["兴业银行股份有限公司", "兴业银行"],
    "民生银行": ["中国民生银行股份有限公司", "民生银行"],
    "光大银行": ["中国光大银行股份有限公司", "光大银行"],
    "平安银行": ["平安银行股份有限公司", "深圳发展银行股份有限公司", "深圳发展银行", "平安银行"],
    "华夏银行": ["华夏银行股份有限公司", "华夏银行"],
    "广发银行": ["广发银行股份有限公司", "广东发展银行股份有限公司", "广东发展银行", "广发银行"],
    "浙商银行": ["浙商银行股份有限公司", "浙商银行"],
    "渤海银行": ["渤海银行股份有限公司", "渤海银行"],
    "恒丰银行": ["恒丰银行股份有限公司", "恒丰银行"],
    "北京银行": ["北京银行股份有限公司", "北京银行"],
    "上海银行": ["上海银行股份有限公司", "上海银行"],
    "江苏银行": ["江苏银行股份有限公司", "江苏银行"],
    "南京银行": ["南京银行股份有限公司", "南京银行"],
    "宁波银行": ["宁波银行股份有限公司", "宁波银行"],
    "杭州银行": ["杭州银行股份有限公司", "杭州银行"],
    "成都银行": ["成都银行股份有限公司", "成都银行"],
    "长沙银行": ["长沙银行股份有限公司", "长沙银行"],
    "贵阳银行": ["贵阳银行股份有限公司", "贵阳银行"],
    "郑州银行": ["郑州银行股份有限公司", "郑州银行"],
    "青岛银行": ["青岛银行股份有限公司", "青岛银行"],
    "苏州银行": ["苏州银行股份有限公司", "苏州银行"],
    "厦门银行": ["厦门银行股份有限公司", "厦门银行"],
    "齐鲁银行": ["齐鲁银行股份有限公司", "齐鲁银行"],
    "兰州银行": ["兰州银行股份有限公司", "兰州银行"],
    "重庆银行": ["重庆银行股份有限公司", "重庆银行"],
    "西安银行": ["西安银行股份有限公司", "西安银行"],
    "上海农商银行": ["上海农村商业银行股份有限公司", "上海农商银行"],
    "重庆农商银行": ["重庆农村商业银行股份有限公司", "重庆农商行", "渝农商行"],
    "青岛农商银行": ["青岛农村商业银行股份有限公司", "青农商行"],
    "紫金银行": ["江苏紫金农村商业银行股份有限公司", "紫金银行"],
    "无锡银行": ["无锡农村商业银行股份有限公司", "无锡银行"],
    "常熟银行": ["江苏常熟农村商业银行股份有限公司", "常熟银行"],
    "江阴银行": ["江苏江阴农村商业银行股份有限公司", "江阴银行"],
    "张家港农商行": ["江苏张家港农村商业银行股份有限公司", "张家港行"],
    "瑞丰银行": ["浙江绍兴瑞丰农村商业银行股份有限公司", "瑞丰银行"],
    "国家开发银行": ["国家开发银行"],
    "农业发展银行": ["中国农业发展银行", "农业发展银行"],
    "进出口银行": ["中国进出口银行", "进出口银行"],
}

GROUP_AFFILIATES = {
    "工银金融资产投资有限公司":"工商银行", "工银理财有限责任公司":"工商银行", "工银瑞信":"工商银行",
    "建信金融资产投资有限公司":"建设银行", "建信理财有限责任公司":"建设银行", "建信基金":"建设银行",
    "农银金融资产投资有限公司":"农业银行", "农银理财有限责任公司":"农业银行", "农银汇理":"农业银行",
    "中银金融资产投资有限公司":"中国银行", "中银理财有限责任公司":"中国银行", "中银基金":"中国银行",
    "交银金融资产投资有限公司":"交通银行", "交银理财有限责任公司":"交通银行", "交银施罗德":"交通银行",
    "招银金融资产投资有限公司":"招商银行", "招银理财有限责任公司":"招商银行", "招商基金管理有限公司":"招商银行",
}

PRODUCT_TERMS = ["证券投资基金","基金","ETF","交易型开放式","资产管理计划","集合资产管理计划","单一资产管理计划","理财产品","养老金产品","企业年金","年金计划","保险产品","专户","集合计划","资管计划"]


def clean(s):
    return re.sub(r"\s+", "", str(s or "").replace("—","－").replace("–","－"))

def code(v):
    m = re.search(r"(\d{6})", str(v or "")); return m.group(1) if m else str(v or "")

def fnum(v):
    if v in (None, ""): return None
    try: return float(str(v).replace(",","").replace("%","").strip())
    except: return None

def inum(v):
    x=fnum(v); return int(x) if x is not None else None

def normalize_bank(text):
    t=clean(text)
    for std, aliases in BANK_ALIASES.items():
        for a in sorted(aliases,key=len,reverse=True):
            if clean(a) in t: return std, True
    pats=[r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40}(?:农村商业银行|农村合作银行|商业银行|银行)(?:股份有限公司|有限责任公司)?)",r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40}(?:农村信用合作联社|农村信用社|信用联社))"]
    for p in pats:
        m=re.search(p,t)
        if m: return m.group(1), False
    return "", False

def classify(name):
    n=clean(name)
    for a,b in GROUP_AFFILIATES.items():
        if clean(a) in n:
            return b,"银行集团关联机构",0,1,1
    bank,known=normalize_bank(n)
    if not bank: return "","非银行",0,0,0
    if any(x.upper() in n.upper() for x in PRODUCT_TERMS):
        return bank,"银行托管或产品账户",0,0,0
    return bank,"银行直接持股",1,1,0 if known else 1

def write_csv(path, rows):
    rows=list(rows); path.parent.mkdir(parents=True,exist_ok=True)
    fields=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen: fields.append(k); seen.add(k)
    if not fields: fields=["无数据"]
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

class Client:
    def __init__(self):
        self.s=requests.Session(); self.s.headers.update({"User-Agent":UA,"Referer":"https://data.eastmoney.com/gdfx/HoldingAnalyse.html"})
    def getj(self,params):
        last=None
        for i in range(7):
            try:
                r=self.s.get(URL,params=params,timeout=45); r.raise_for_status(); j=r.json()
                if not isinstance(j,dict) or j.get("result") is None: raise RuntimeError(str(j)[:400])
                return j
            except Exception as e:
                last=e; time.sleep(min(25,1.3*(2**i))+random.random())
        raise RuntimeError(last)
    def date(self,d):
        p={"sortColumns":"NOTICE_DATE,SECURITY_CODE,RANK","sortTypes":"-1,1,1","pageSize":"500","pageNumber":"1","reportName":"RPT_CUSTOM_DMSK_HOLDERS_JOIN_HOLDER_SHAREANALYSIS","columns":"ALL","source":"WEB","client":"WEB","filter":f"(END_DATE='{d}')"}
        j=self.getj(p); result=j.get("result") or {}; pages=int(result.get("pages") or 0); out=[]
        logging.info("%s pages=%s total=%s",d,pages,result.get("count"))
        for pg in range(1,pages+1):
            p["pageNumber"]=str(pg); data=(self.getj(p).get("result") or {}).get("data") or []; out.extend(data)
            logging.info("%s page %d/%d rows=%d",d,pg,pages,len(out)); time.sleep(.15)
        return out

def transform(x,year):
    name=str(x.get("HOLDER_NAME") or "").strip(); bank,typ,direct,broad,review=classify(name)
    ratio=x.get("HOLD_RATIO")
    if ratio is None: ratio=x.get("HOLD_RATIO_ORG") or x.get("HOLDNUM_RATIO")
    rank=x.get("RANK") if x.get("RANK") is not None else x.get("HOLDER_RANK")
    return {"报告年度":year,"报告期":str(x.get("END_DATE") or f"{year}-12-31")[:10],"证券代码":code(x.get("SECURITY_CODE") or x.get("SECUCODE")),"证券简称":x.get("SECURITY_NAME_ABBR") or "","股东排名":inum(rank),"股东原始名称":name,"股东类型":x.get("HOLDER_NEWTYPE") or x.get("HOLDER_TYPE_ORG") or x.get("HOLDER_TYPE") or "","持股数量（股）":inum(x.get("HOLD_NUM")),"持股比例（%）":fnum(ratio),"公告日期":str(x.get("NOTICE_DATE") or x.get("UPDATE_DATE") or "")[:10],"标准化银行名称":bank,"银行关系类型":typ,"是否银行直接持股":direct,"是否银行集团宽口径":broad,"是否需人工复核":review,"数据来源":"东方财富数据中心-十大股东"}

def dedupe(rows):
    best={}
    for r in rows:
        k=(r.get("报告年度"),r.get("证券代码"),r.get("股东排名"),clean(r.get("股东原始名称")))
        if k not in best or str(r.get("公告日期") or "")>str(best[k].get("公告日期") or ""): best[k]=r
    return sorted(best.values(),key=lambda r:(int(r.get("报告年度") or 0),str(r.get("证券代码") or ""),int(r.get("股东排名") or 999)))

def longest(ys):
    ys=sorted(set(ys))
    if not ys:return 0,""
    bl=cl=1; bs=cs=p=ys[0]; be=ys[0]
    for y in ys[1:]:
        if y==p+1: cl+=1
        else:
            if cl>bl: bl,bs,be=cl,cs,p
            cs,cl=y,1
        p=y
    if cl>bl: bl,bs,be=cl,cs,p
    return bl, f"{bs}—{be}" if bl>1 else str(bs)

def outputs(rows):
    direct=[r for r in rows if int(r.get("是否银行直接持股") or 0)==1]
    g=defaultdict(list)
    for r in direct:
        if r.get("证券代码") and r.get("标准化银行名称"): g[(r["证券代码"],int(r["报告年度"]),r["标准化银行名称"])].append(r)
    fby=[]
    for (c,y,b),rs in sorted(g.items()):
        ratios=[fnum(r.get("持股比例（%）")) for r in rs]; ratios=[x for x in ratios if x is not None]
        shares=[inum(r.get("持股数量（股）")) for r in rs]; shares=[x for x in shares if x is not None]
        ranks=[inum(r.get("股东排名")) for r in rs]; ranks=[x for x in ranks if x is not None]
        fby.append({"证券代码":c,"证券简称":next((r.get("证券简称") for r in rs if r.get("证券简称")),""),"报告年度":y,"标准化银行名称":b,"银行股东原始名称":"；".join(sorted({r.get("股东原始名称","") for r in rs if r.get("股东原始名称")})),"银行股东最好排名":min(ranks) if ranks else "","银行持股比例（%）":max(ratios) if ratios else "","银行持股数量（股）":max(shares) if shares else "","是否银行直接持股":1})
    dg=defaultdict(list)
    for r in fby: dg[(r["证券代码"],r["标准化银行名称"])].append(r)
    duration=[]
    for (c,b),rs in sorted(dg.items()):
        ys=sorted({int(r["报告年度"]) for r in rs}); first,last=min(ys),max(ys); lc,li=longest(ys)
        ratios=[fnum(r.get("银行持股比例（%）")) for r in rs]; ratios=[x for x in ratios if x is not None]
        seq="；".join(f"{r['报告年度']}:{r.get('银行持股比例（%）','')}%" for r in sorted(rs,key=lambda z:int(z["报告年度"])))
        duration.append({"证券代码":c,"证券简称":next((r.get("证券简称") for r in rs if r.get("证券简称")),""),"标准化银行名称":b,"首次进入前十大股东年份":first,"最后进入前十大股东年份":last,"出现年份列表":"、".join(map(str,ys)),"累计出现年数":len(ys),"最长连续出现年数":lc,"最长连续出现区间":li,"首末年份跨度":last-first+1,"是否连续出现":1 if len(ys)==last-first+1 else 0,"年度持股比例序列":seq,"平均可观测持股比例（%）":round(sum(ratios)/len(ratios),6) if ratios else "","口径说明":"前十大股东可观测持股时长，不等同于真实持股存续期"})
    cy={}
    for r in rows:
        if r.get("证券代码"): cy[(r["证券代码"],int(r["报告年度"]))]=r.get("证券简称","")
    by=defaultdict(list)
    for r in fby: by[(r["证券代码"],int(r["报告年度"]))].append(r)
    panel=[]
    for (c,y),n in sorted(cy.items(),key=lambda z:(z[0][1],z[0][0])):
        rs=by.get((c,y),[]); ratios=[fnum(r.get("银行持股比例（%）")) for r in rs]; ratios=[x for x in ratios if x is not None]
        panel.append({"报告年度":y,"证券代码":c,"证券简称":n,"前十大股东中是否有银行":1 if rs else 0,"前十大股东中的银行数量":len({r.get("标准化银行名称") for r in rs if r.get("标准化银行名称")}),"银行直接持股比例合计（%）":round(sum(ratios),6) if ratios else 0,"最大单家银行持股比例（%）":max(ratios) if ratios else 0,"银行名称列表":"；".join(sorted({r.get("标准化银行名称","") for r in rs if r.get("标准化银行名称")}))})
    return direct,fby,duration,panel

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--start-year",type=int,default=2010); ap.add_argument("--end-year",type=int,default=2023); ap.add_argument("--output",type=Path,default=Path("results_shareholders")); a=ap.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    a.output.mkdir(parents=True,exist_ok=True); c=Client(); allr=[]; errors=[]
    for y in range(a.start_year,a.end_year+1):
        try:
            raw=c.date(f"{y}-12-31"); rr=dedupe([transform(x,y) for x in raw]); rr=[r for r in rr if r.get("股东排名") in (None,"") or 1<=int(r["股东排名"])<=10]; allr.extend(rr); logging.info("year=%d rows=%d direct_bank=%d",y,len(rr),sum(int(r.get("是否银行直接持股") or 0) for r in rr))
        except Exception as e:
            logging.exception("year %d failed",y); errors.append({"报告年度":y,"错误":repr(e)})
    allr=dedupe(allr); direct,fby,duration,panel=outputs(allr); review=[r for r in allr if int(r.get("是否需人工复核") or 0)==1]
    write_csv(a.output/"01_前十大股东全量_2010_2023.csv",allr); write_csv(a.output/"02_前十大股东中银行明细_2010_2023.csv",direct); write_csv(a.output/"03_公司银行年度持股明细_2010_2023.csv",fby); write_csv(a.output/"04_银行持股年份及可观测时长_2010_2023.csv",duration); write_csv(a.output/"05_公司年度银行股东指标_2010_2023.csv",panel); write_csv(a.output/"98_人工复核队列.csv",review); write_csv(a.output/"99_错误日志.csv",errors)
    summary={"起始年份":a.start_year,"结束年份":a.end_year,"前十大股东记录数":len(allr),"银行直接持股记录数":len(direct),"公司-银行-年份记录数":len(fby),"公司-银行持股时长记录数":len(duration),"公司-年份记录数":len(panel),"人工复核记录数":len(review),"失败年份数":len(errors)}
    (a.output/"00_运行摘要.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
