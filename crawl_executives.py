#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, json, logging, random, re, subprocess, tempfile, time
from collections import defaultdict
from pathlib import Path
import requests

CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "https://static.cninfo.com.cn/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
HEADERS = {"User-Agent":UA,"Referer":"https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice","X-Requested-With":"XMLHttpRequest"}

BANK_ALIASES = {
    "工商银行":["中国工商银行股份有限公司","中国工商银行","工商银行"],
    "建设银行":["中国建设银行股份有限公司","中国建设银行","建设银行"],
    "农业银行":["中国农业银行股份有限公司","中国农业银行","农业银行"],
    "中国银行":["中国银行股份有限公司","中国银行"],
    "交通银行":["交通银行股份有限公司","交通银行"],
    "邮储银行":["中国邮政储蓄银行股份有限公司","中国邮政储蓄银行","邮储银行"],
    "招商银行":["招商银行股份有限公司","招商银行"],
    "浦发银行":["上海浦东发展银行股份有限公司","浦发银行"],
    "中信银行":["中信银行股份有限公司","中信银行"],
    "兴业银行":["兴业银行股份有限公司","兴业银行"],
    "民生银行":["中国民生银行股份有限公司","民生银行"],
    "光大银行":["中国光大银行股份有限公司","光大银行"],
    "平安银行":["平安银行股份有限公司","深圳发展银行股份有限公司","深圳发展银行","平安银行"],
    "华夏银行":["华夏银行股份有限公司","华夏银行"],
    "广发银行":["广发银行股份有限公司","广东发展银行股份有限公司","广东发展银行","广发银行"],
    "浙商银行":["浙商银行股份有限公司","浙商银行"],
    "渤海银行":["渤海银行股份有限公司","渤海银行"],
    "恒丰银行":["恒丰银行股份有限公司","恒丰银行"],
    "北京银行":["北京银行股份有限公司","北京银行"],
    "上海银行":["上海银行股份有限公司","上海银行"],
    "江苏银行":["江苏银行股份有限公司","江苏银行"],
    "南京银行":["南京银行股份有限公司","南京银行"],
    "宁波银行":["宁波银行股份有限公司","宁波银行"],
    "杭州银行":["杭州银行股份有限公司","杭州银行"],
    "成都银行":["成都银行股份有限公司","成都银行"],
    "长沙银行":["长沙银行股份有限公司","长沙银行"],
    "贵阳银行":["贵阳银行股份有限公司","贵阳银行"],
    "郑州银行":["郑州银行股份有限公司","郑州银行"],
    "青岛银行":["青岛银行股份有限公司","青岛银行"],
    "苏州银行":["苏州银行股份有限公司","苏州银行"],
    "厦门银行":["厦门银行股份有限公司","厦门银行"],
    "齐鲁银行":["齐鲁银行股份有限公司","齐鲁银行"],
    "兰州银行":["兰州银行股份有限公司","兰州银行"],
    "重庆银行":["重庆银行股份有限公司","重庆银行"],
    "西安银行":["西安银行股份有限公司","西安银行"],
    "上海农商银行":["上海农村商业银行股份有限公司","上海农商银行"],
    "重庆农商银行":["重庆农村商业银行股份有限公司","重庆农商行","渝农商行"],
    "青岛农商银行":["青岛农村商业银行股份有限公司","青农商行"],
    "国家开发银行":["国家开发银行"],
    "农业发展银行":["中国农业发展银行","农业发展银行"],
    "进出口银行":["中国进出口银行","进出口银行"],
}

BANK_TERMS = ["银行","分行","支行","农村信用社","信用联社","农商行","农商银行","国家开发银行","农业发展银行","进出口银行"]
ROLE_TERMS = ["董事长","副董事长","董事","独立董事","监事会主席","监事","总经理","总裁","副总经理","副总裁","财务总监","财务负责人","董事会秘书","董秘","高级管理人员"]
EMPLOY_VERBS = ["曾任","历任","任职于","任职","就职于","就职","工作于","曾在","先后任","担任","现任","兼任","现兼任","目前任"]
BANK_JOB_TERMS = ["行长","副行长","支行长","经理","副经理","总经理","副总经理","主任","副主任","处长","副处长","科长","客户经理","信贷员","职员","总监","负责人","董事","监事"]
EXCLUDE_TITLE = ["摘要","英文版","社会责任报告","可持续发展报告","ESG","问询","审计报告","鉴证报告","内部控制"]


def clean(s):
    return re.sub(r"\s+","",str(s or "").replace("—","－").replace("–","－"))

def normalize_bank(text):
    t=clean(text)
    found=[]
    for std,aliases in BANK_ALIASES.items():
        if any(clean(a) in t for a in sorted(aliases,key=len,reverse=True)): found.append(std)
    # generic banks not in dictionary
    pats=[r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40}(?:农村商业银行|农村合作银行|商业银行|银行)(?:股份有限公司|有限责任公司)?)",r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40}(?:农村信用合作联社|农村信用社|信用联社))"]
    for p in pats:
        for m in re.finditer(p,t):
            x=m.group(1)
            if x not in found and not any(x in clean(a) or clean(a) in x for vv in BANK_ALIASES.values() for a in vv): found.append(x)
    return found

def write_csv(path, rows):
    rows=list(rows); path.parent.mkdir(parents=True,exist_ok=True)
    fields=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen: fields.append(k); seen.add(k)
    if not fields: fields=["无数据"]
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

class Cninfo:
    def __init__(self):
        self.s=requests.Session(); self.s.headers.update(HEADERS)
    def post(self,data):
        last=None
        for i in range(6):
            try:
                r=self.s.post(CNINFO_QUERY,data=data,timeout=45); r.raise_for_status(); j=r.json()
                if not isinstance(j,dict): raise RuntimeError("non-json")
                return j
            except Exception as e:
                last=e; time.sleep(min(20,1.5*(2**i))+random.random())
        raise RuntimeError(last)
    def reports(self,year,column):
        out=[]; page=1
        while True:
            data={"pageNum":page,"pageSize":30,"column":column,"tabName":"fulltext","plate":"","stock":"","searchkey":f"{year}年年度报告","secid":"","category":"category_ndbg_szsh;","trade":"","seDate":f"{year+1}-01-01~{year+2}-08-31","sortName":"","sortType":"","isHLtitle":"true"}
            j=self.post(data); anns=j.get("announcements") or []
            if not anns: break
            for a in anns:
                title=re.sub(r"<.*?>","",a.get("announcementTitle") or "")
                ct=clean(title)
                if f"{year}年年度报告" not in ct: continue
                if any(x in title for x in EXCLUDE_TITLE): continue
                adjunct=str(a.get("adjunctUrl") or "")
                if not adjunct.lower().endswith(".pdf"): continue
                out.append({"报告年度":year,"证券代码":str(a.get("secCode") or ""),"证券简称":str(a.get("secName") or ""),"年报标题":title,"公告时间":int(a.get("announcementTime") or 0),"公告ID":str(a.get("announcementId") or ""),"数据来源链接":CNINFO_PDF+adjunct.lstrip("/"),"交易所查询列":column})
            hasmore=j.get("hasMore")
            total=int(j.get("totalpages") or j.get("totalPages") or 0)
            if hasmore is False or (total and page>=total) or len(anns)<30: break
            page+=1; time.sleep(random.uniform(.35,.65))
        return out
    def download(self,url,path):
        last=None
        for i in range(5):
            try:
                with self.s.get(url,timeout=90,stream=True) as r:
                    r.raise_for_status(); first=True
                    with path.open("wb") as f:
                        for chunk in r.iter_content(1024*1024):
                            if chunk:
                                if first and not chunk.startswith(b"%PDF"): raise RuntimeError("not pdf")
                                first=False; f.write(chunk)
                return
            except Exception as e:
                last=e
                try: path.unlink()
                except: pass
                time.sleep(min(15,1.5*(2**i))+random.random())
        raise RuntimeError(last)

def score_title(title):
    s=0
    if "修订" in title or "更新" in title: s+=20
    if "更正" in title: s+=10
    return s

def dedupe_reports(rows):
    best={}
    for r in rows:
        k=(r["报告年度"],r["证券代码"]); old=best.get(k)
        if old is None or (score_title(r["年报标题"]),r["公告时间"])>(score_title(old["年报标题"]),old["公告时间"]): best[k]=r
    return sorted(best.values(),key=lambda r:r["证券代码"])

def pdf_text(pdf):
    p=subprocess.run(["pdftotext","-layout","-enc","UTF-8",str(pdf),"-"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    if p.returncode!=0: raise RuntimeError(p.stderr.decode("utf-8","ignore")[:400])
    return p.stdout.decode("utf-8","ignore")

NAME_RE=re.compile(r"(?:^|\n|[。；;])\s*(?:\d{1,2}[、\.．]\s*)?([\u4e00-\u9fa5·]{2,8})(先生|女士)[：:,，]?",re.M)

def extract_candidates(text,meta):
    if not any(t in text for t in BANK_TERMS): return []
    ms=list(NAME_RE.finditer(text)); out=[]; seen=set()
    for i,m in enumerate(ms):
        start=m.start(); end=ms[i+1].start() if i+1<len(ms) else min(len(text),start+3500)
        if end-start>5000: end=start+5000
        bio=text[start:end]
        compact=re.sub(r"\s+","",bio)
        if not any(t in compact for t in BANK_TERMS): continue
        # 确认是董监高简介而非正文其他人物
        if not any(t in compact[:1200] for t in ROLE_TERMS): continue
        banks=normalize_bank(compact)
        if not banks: banks=["未标准化银行"]
        hist=bool(re.search(r"(?:曾任|历任|任职于|任职|就职于|就职|工作于|曾在|先后任).{0,220}(?:银行|分行|支行|信用社|信用联社|农商)",compact))
        current=bool(re.search(r"(?:现任|兼任|现兼任|目前任|担任).{0,220}(?:银行|分行|支行|信用社|信用联社|农商)",compact))
        role_after_bank=bool(re.search(r"(?:银行|分行|支行|信用社|信用联社|农商).{0,120}(?:行长|副行长|支行长|经理|总经理|主任|处长|科长|客户经理|信贷员|职员|总监|负责人|董事|监事)",compact))
        high=(hist or current or role_after_bank) and banks!=["未标准化银行"]
        for bank in banks:
            k=(m.group(1),bank)
            if k in seen: continue
            seen.add(k)
            out.append({"报告年度":meta["报告年度"],"证券代码":meta["证券代码"],"证券简称":meta["证券简称"],"高管姓名":m.group(1),"标准化银行名称":bank,"是否银行背景候选":1,"是否高置信银行背景":1 if high else 0,"是否当前银行联结":1 if current and high else 0,"是否历史银行任职":1 if (hist or role_after_bank) and high else 0,"是否需人工复核":0 if high else 1,"高管简历原文/上下文":re.sub(r"\s+"," ",bio).strip()[:2600],"年报标题":meta["年报标题"],"数据来源链接":meta["数据来源链接"],"定义口径":"报告期内在任董监高公开简历存在银行机构正式现任或历史任职经历"})
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True); ap.add_argument("--output",type=Path,required=True); args=ap.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s"); args.output.mkdir(parents=True,exist_ok=True)
    c=Cninfo(); errors=[]; reports=[]
    for col in ["szse","sse"]:
        try:
            rr=c.reports(args.year,col); logging.info("%s %s reports=%d",args.year,col,len(rr)); reports.extend(rr)
        except Exception as e:
            logging.exception("query failed %s",col); errors.append({"报告年度":args.year,"阶段":"年报清单","交易所":col,"错误":repr(e)})
    reports=dedupe_reports(reports); write_csv(args.output/"01_年报清单.csv",reports); logging.info("dedup reports=%d",len(reports))
    cand=[]
    with tempfile.TemporaryDirectory() as td:
        td=Path(td)
        for idx,meta in enumerate(reports,1):
            try:
                pdf=td/f"{meta['证券代码']}.pdf"; c.download(meta["数据来源链接"],pdf); text=pdf_text(pdf); rows=extract_candidates(text,meta); cand.extend(rows)
                if idx%25==0: logging.info("%s progress %d/%d candidates=%d",args.year,idx,len(reports),len(cand))
                time.sleep(random.uniform(.15,.35))
            except Exception as e:
                logging.warning("fail %s %s %r",meta["证券代码"],meta["证券简称"],e); errors.append({"报告年度":args.year,"证券代码":meta["证券代码"],"证券简称":meta["证券简称"],"阶段":"PDF解析","错误":repr(e),"数据来源链接":meta["数据来源链接"]})
    # dedupe candidate by firm-year-name-bank
    best={}
    for r in cand:
        k=(r["证券代码"],r["报告年度"],r["高管姓名"],r["标准化银行名称"]); old=best.get(k)
        if old is None or int(r["是否高置信银行背景"])>int(old["是否高置信银行背景"]): best[k]=r
    cand=list(best.values()); high=[r for r in cand if int(r["是否高置信银行背景"])==1]; review=[r for r in cand if int(r["是否需人工复核"])==1]
    write_csv(args.output/"02_高管银行背景候选.csv",cand); write_csv(args.output/"03_高置信银行背景高管明细.csv",high); write_csv(args.output/"04_人工复核队列.csv",review); write_csv(args.output/"99_错误日志.csv",errors)
    g=defaultdict(list)
    for r in high: g[(r["证券代码"],r["报告年度"])].append(r)
    panel=[]
    for (code,year),rs in sorted(g.items()):
        panel.append({"报告年度":year,"证券代码":code,"证券简称":next((r["证券简称"] for r in rs if r["证券简称"]),""),"是否存在高管银行背景":1,"银行背景高管人数":len({r["高管姓名"] for r in rs}),"高管关联银行数量":len({r["标准化银行名称"] for r in rs}),"是否存在当前银行联结":1 if any(int(r["是否当前银行联结"]) for r in rs) else 0,"是否存在历史银行任职":1 if any(int(r["是否历史银行任职"]) for r in rs) else 0,"关联银行列表":"；".join(sorted({r["标准化银行名称"] for r in rs}))})
    write_csv(args.output/"05_公司年度高管银行背景指标_有背景样本.csv",panel)
    summary={"报告年度":args.year,"年报清单数":len(reports),"银行背景候选记录数":len(cand),"高置信银行背景记录数":len(high),"有银行背景公司数":len(panel),"人工复核记录数":len(review),"错误记录数":len(errors)}
    (args.output/"00_运行摘要.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
