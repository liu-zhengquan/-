#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse,csv,json,logging,re,tempfile,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import defaultdict
from pathlib import Path
import requests, akshare as ak
from repair_executive_bank_history import read_csv,write_csv,strict_clean_row,dedupe_details,parse_report

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
H={'User-Agent':UA,'Referer':'https://www.cninfo.com.cn/'}


def pdf_from_detail(url):
    s=requests.Session(); s.headers.update(H)
    try:
        r=s.get(url,timeout=30,allow_redirects=True); r.raise_for_status(); t=r.text
    except Exception:
        return ''
    pats=[r'https?://static\.cninfo\.com\.cn/[^\"\'<> ]+?\.pdf',r'(?P<u>finalpage/[^\"\'<> ]+?\.pdf)',r'(?P<u>files/[^\"\'<> ]+?\.pdf)']
    for p in pats:
        m=re.search(p,t,re.I)
        if m:
            u=m.group(0) if m.group(0).startswith('http') else 'https://static.cninfo.com.cn/'+(m.groupdict().get('u') or m.group(0)).lstrip('/')
            return u.replace('\\/','/')
    return ''


def query_one(year,row):
    code=row['证券代码']; start=f'{year+1}0101'; end=f'{year+2}1231'
    last=None
    for category in ['年报','']:
        for keyword in ['',f'{year}年年度报告',f'{year}年度报告']:
            try:
                df=ak.stock_zh_a_disclosure_report_cninfo(symbol=code,market='沪深京',keyword=keyword,category=category,start_date=start,end_date=end)
                if df is None or df.empty: continue
                cand=[]
                for _,x in df.iterrows():
                    title=str(x.get('公告标题') or '')
                    if (f'{year}年年度报告' not in title and f'{year}年度报告' not in title): continue
                    if any(z in title for z in ['摘要','英文版','社会责任报告','可持续发展报告','ESG','问询','审计报告','鉴证报告','内部控制']): continue
                    url=str(x.get('公告链接') or '')
                    pdf=pdf_from_detail(url)
                    if not pdf: continue
                    cand.append({'报告年度':year,'证券代码':code,'证券简称':str(x.get('简称') or row.get('证券简称','')),'年报标题':title,'公告时间':str(x.get('公告时间') or ''),'公告ID':'','数据来源链接':pdf,'交易所查询列':'AKShare-CNINFO'})
                if cand:
                    cand.sort(key=lambda z:(int('修订' in z['年报标题'] or '更新' in z['年报标题']),int('更正' in z['年报标题']),str(z['公告时间'])))
                    return cand[-1]
            except Exception as e:
                last=e; time.sleep(1.5)
    return None


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--input-dir',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args(); y=a.year; inp=a.input_dir; out=a.output; out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    sample=read_csv(inp/'01_样本公司清单_剔除ST金融业.csv')
    # tolerate either final/fallback or repaired filenames
    rfile=inp/'02_年报清单_最终补查.csv'
    if not rfile.exists(): rfile=inp/'02_年报清单_修复后.csv'
    dfile=inp/'03_高管银行任职分段明细_严格.csv'
    reports=read_csv(rfile); strict_old=read_csv(dfile)
    kept=[r for r in sample if str(r.get('最终是否纳入样本'))=='1']; by={r['证券代码']:r for r in reports}; missing=[r for r in kept if r['证券代码'] not in by]
    logging.info('%s kept=%d covered=%d missing=%d',y,len(kept),len(by),len(missing))
    new=[]; errors=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut={ex.submit(query_one,y,r):r for r in missing}
        for i,f in enumerate(as_completed(fut),1):
            r=fut[f]
            try:
                x=f.result();
                if x:new.append(x)
            except Exception as e: errors.append({'报告年度':y,'证券代码':r['证券代码'],'阶段':'AKShare补查','错误':repr(e)})
            if i%25==0: logging.info('%s akshare fallback %d/%d new=%d',y,i,len(missing),len(new))
    for r in new: by[r['证券代码']]=r
    raw_new=[]; parsed_new=set()
    with tempfile.TemporaryDirectory() as td:
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut={ex.submit(parse_report,m,td):m for m in new}
            for f in as_completed(fut):
                m=fut[f]
                try:
                    rr=f.result(); raw_new.extend(rr); parsed_new.add(m['证券代码'])
                except Exception as e: errors.append({'报告年度':y,'证券代码':m['证券代码'],'阶段':'AKShare补查_PDF','错误':repr(e),'数据来源链接':m.get('数据来源链接','')})
    strict_new=[]
    for r in raw_new:
        x=strict_clean_row(r)
        if x: strict_new.append(x)
    strict=dedupe_details(strict_old+strict_new); reviews=[r for r in strict if str(r.get('是否需人工复核'))=='1']
    g=defaultdict(list)
    for r in strict:g[r['证券代码']].append(r)
    parsed_old={r['证券代码'] for r in reports}; parsed=parsed_old|parsed_new
    panel=[]
    for r in kept:
        c=r['证券代码']; rs=g.get(c,[]); ok=c in parsed; found=c in by
        panel.append({'报告年度':y,'证券代码':c,'证券简称':r.get('证券简称',''),'年报是否找到':1 if found else 0,'年报是否成功解析':1 if ok else 0,
        '是否存在高管银行关联':(1 if rs else 0) if ok else '', '银行背景高管人数':len({x['高管姓名'] for x in rs}) if ok else '', '关联银行数量':len({x['标准化银行名称'] for x in rs}) if ok else '',
        '是否存在历史银行任职':1 if ok and any(str(x.get('是否历史银行任职'))=='1' for x in rs) else (0 if ok else ''),'是否存在当前银行联结':1 if ok and any(str(x.get('是否当前银行联结'))=='1' for x in rs) else (0 if ok else ''),
        '银行背景高管名单':'；'.join(sorted({x['高管姓名'] for x in rs})) if ok else '', '关联银行列表':'；'.join(sorted({x['标准化银行名称'] for x in rs})) if ok else '',
        '样本口径':'沪深A股；剔除当年ST/退市整理及当年金融业；未覆盖公司不编码为0'})
    write_csv(out/'01_样本公司清单_剔除ST金融业.csv',sample); write_csv(out/'02_年报清单_最终补查.csv',sorted(by.values(),key=lambda r:r['证券代码']))
    write_csv(out/'03_高管银行任职分段明细_严格.csv',strict); write_csv(out/'04_人工复核队列_严格.csv',reviews); write_csv(out/'05_公司年度高管银行背景面板_严格.csv',panel); write_csv(out/'99_错误日志.csv',errors)
    summary={'报告年度':y,'最终样本公司数':len(kept),'补查前年报覆盖数':len(reports),'补查后年报覆盖数':len(by),'补查后年报覆盖率（%）':round(100*len(by)/len(kept),2),'新增年报数':len(new),'严格银行任职记录数':len(strict),'有银行背景公司数_严格':len({r['证券代码'] for r in strict}),'人工复核记录数_严格':len(reviews),'错误记录数':len(errors)}
    (out/'00_运行摘要_最终补查.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
