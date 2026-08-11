#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse,csv,json,logging,re,time,random
from pathlib import Path
from collections import defaultdict
import requests


def read_csv(p):
    if not p.exists(): return []
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_csv(p,rows):
    rows=list(rows); p.parent.mkdir(parents=True,exist_ok=True)
    fields=[];seen=set()
    for r in rows:
        for k in r:
            if k not in seen: fields.append(k);seen.add(k)
    if not fields:fields=['无数据']
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def listing_dates():
    # Eastmoney A-share universe. f26 is listing date (YYYYMMDD).
    url='https://82.push2.eastmoney.com/api/qt/clist/get'
    params={'pn':1,'pz':10000,'po':1,'np':1,'fltt':2,'invt':2,'fid':'f12',
            'fs':'m:0+t:6,m:0+t:80,m:0+t:81+s:2048,m:1+t:2,m:1+t:23,m:1+t:8',
            'fields':'f12,f14,f26'}
    h={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
    last=None
    for i in range(6):
        try:
            r=requests.get(url,params=params,headers=h,timeout=45);r.raise_for_status();j=r.json()
            diff=((j.get('data') or {}).get('diff') or [])
            out={}
            for x in diff:
                c=str(x.get('f12') or '')
                d=str(x.get('f26') or '').replace('-','')
                if re.fullmatch(r'\d{6}',c) and re.fullmatch(r'\d{8}',d):out[c]=d
            if len(out)<3000: raise RuntimeError(f'listing map too small: {len(out)}')
            return out
        except Exception as e:
            last=e;time.sleep(min(15,1.5*(2**i))+random.random())
    raise RuntimeError(last)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();y=a.year;inp=a.input_dir;out=a.output;out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    lm=listing_dates(); cutoff=f'{y}1231'
    sample=read_csv(inp/'01_样本公司清单_剔除ST金融业.csv')
    rep_candidates=[inp/'02_年报清单_全市场补查.csv',inp/'02_年报清单_最终补查.csv',inp/'02_年报清单_修复后.csv',inp/'02_年报清单.csv']
    reports=[]
    for p in rep_candidates:
        if p.exists(): reports=read_csv(p); break
    det=read_csv(inp/'03_高管银行任职分段明细_严格.csv')
    rev=read_csv(inp/'04_人工复核队列_严格.csv')
    oldpanel=read_csv(inp/'05_公司年度高管银行背景面板_严格.csv')
    errors=read_csv(inp/'99_错误日志.csv')
    corrected=[]; removed=[]; missing_date=[]
    for r in sample:
        rr=dict(r);c=rr.get('证券代码','');d=lm.get(c,'')
        rr['上市日期']=d
        rr['年末是否已上市']='1' if d and d<=cutoff else ('0' if d else '')
        if d and d>cutoff:
            rr['最终是否纳入样本']=0;rr['剔除原因']='年末尚未上市';removed.append(rr)
        elif not d:
            missing_date.append(c)
        corrected.append(rr)
    kept=[r for r in corrected if str(r.get('最终是否纳入样本'))=='1']
    keptcodes={r['证券代码'] for r in kept}
    reports=[r for r in reports if r.get('证券代码') in keptcodes]
    det=[r for r in det if r.get('证券代码') in keptcodes]
    rev=[r for r in rev if r.get('证券代码') in keptcodes]
    rcodes={r.get('证券代码') for r in reports}; g=defaultdict(list)
    for r in det:g[r['证券代码']].append(r)
    oldp={r.get('证券代码'):r for r in oldpanel}
    panel=[]
    for s in kept:
        c=s['证券代码']; rs=g.get(c,[]);found=c in rcodes
        op=oldp.get(c,{})
        parsed=str(op.get('年报是否成功解析',''))=='1' if op else found
        panel.append({'报告年度':y,'证券代码':c,'证券简称':s.get('证券简称',''),'上市日期':s.get('上市日期',''),'年报是否找到':1 if found else 0,'年报是否成功解析':1 if parsed else 0,
            '是否存在高管银行关联':(1 if rs else 0) if parsed else '', '银行背景高管人数':len({x.get('高管姓名','') for x in rs if x.get('高管姓名')}) if parsed else '',
            '关联银行数量':len({x.get('标准化银行名称','') for x in rs if x.get('标准化银行名称')}) if parsed else '',
            '是否存在历史银行任职':1 if parsed and any(str(x.get('是否历史银行任职'))=='1' for x in rs) else (0 if parsed else ''),
            '是否存在当前银行联结':1 if parsed and any(str(x.get('是否当前银行联结'))=='1' for x in rs) else (0 if parsed else ''),
            '银行背景高管名单':'；'.join(sorted({x.get('高管姓名','') for x in rs if x.get('高管姓名')})) if parsed else '',
            '关联银行列表':'；'.join(sorted({x.get('标准化银行名称','') for x in rs if x.get('标准化银行名称')})) if parsed else '',
            '样本口径':'沪深A股；上市日期不晚于当年12月31日；剔除当年ST/退市整理及当年金融业；未覆盖公司不编码为0'})
    cov=round(100*len(rcodes)/len(kept),2) if kept else 0
    summary={'报告年度':y,'原样本公司数':sum(1 for r in sample if str(r.get('最终是否纳入样本'))=='1'),'剔除年末尚未上市公司数':len(removed),'上市日期缺失代码数':len(set(missing_date)),
             '修正后最终样本公司数':len(kept),'年报覆盖公司数':len(rcodes),'年报覆盖率（%）':cov,'严格银行任职记录数':len(det),'有银行背景公司数_严格':len({r.get('证券代码') for r in det}),'人工复核记录数_严格':len(rev),'错误记录数':len(errors)}
    write_csv(out/'01_样本公司清单_上市日期修正.csv',corrected);write_csv(out/'02_年报清单_最终.csv',sorted(reports,key=lambda r:r.get('证券代码','')))
    write_csv(out/'03_高管银行任职分段明细_严格.csv',det);write_csv(out/'04_人工复核队列_严格.csv',rev);write_csv(out/'05_公司年度高管银行背景面板_严格.csv',panel);write_csv(out/'99_错误日志.csv',errors)
    (out/'00_运行摘要_上市日期修正.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
