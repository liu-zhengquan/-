#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse,csv,json,logging,re,time,random
from pathlib import Path
from collections import defaultdict
import requests

A_PREFIX=("000","001","002","003","300","301","600","601","603","605","688","689")

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

def norm_date(v):
    s=str(v or '').strip()
    if not s or s.lower() in {'nan','nat','none'}: return ''
    m=re.search(r'((?:19|20)\d{2})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})',s)
    if m:return f'{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}'
    d=re.sub(r'\D','',s)
    return d[:8] if re.fullmatch(r'(?:19|20)\d{6}',d[:8]) else ''

def valid_code(c):
    return bool(re.fullmatch(r'\d{6}',c)) and c.startswith(A_PREFIX)

def retry_call(fn,label,tries=10):
    last=None
    for i in range(tries):
        try:
            x=fn()
            if x is None or getattr(x,'empty',False): raise RuntimeError(f'{label} empty')
            logging.info('%s rows=%d',label,len(x));return x
        except Exception as e:
            last=e;logging.warning('%s attempt %d failed: %r',label,i+1,e)
            time.sleep(min(25,1.5*(2**min(i,4)))+random.uniform(.5,2.0))
    raise RuntimeError(f'{label} failed: {last!r}')

def listing_dates_exchange():
    import akshare as ak
    out={};src={};fails=[]
    def add(df,code_col,date_col,source):
        n0=len(out)
        for _,r in df.iterrows():
            c=str(r.get(code_col,'')).strip();d=norm_date(r.get(date_col,''))
            if valid_code(c) and d:
                if c not in out or d<out[c]:out[c]=d;src[c]=source
        logging.info('%s added=%d total=%d',source,len(out)-n0,len(out))
    sources=[
      ('SSE main A',lambda:ak.stock_info_sh_name_code(symbol='主板A股'),'证券代码','上市日期','上交所主板A股'),
      ('SSE STAR',lambda:ak.stock_info_sh_name_code(symbol='科创板'),'证券代码','上市日期','上交所科创板'),
      ('SZSE A',lambda:ak.stock_info_sz_name_code(symbol='A股列表'),'A股代码','A股上市日期','深交所A股'),
      ('SSE delist',lambda:ak.stock_info_sh_delist(symbol='全部'),'公司代码','上市日期','上交所暂停/终止上市'),
      ('SZSE delist',lambda:ak.stock_info_sz_delist(symbol='终止上市公司'),'证券代码','上市日期','深交所终止上市'),
    ]
    for label,fn,cc,dc,source in sources:
        try:add(retry_call(fn,label,tries=8),cc,dc,source)
        except Exception as e:
            fails.append(f'{label}:{e!r}');logging.warning('%s unavailable; continue with other listing sources',label)
    # Active SSE/SZSE lists alone should cover >4,500 current A shares and are sufficient
    # to remove future-listed firms from historical annual universes. Delisted lists are additive.
    if len(out)<4300: raise RuntimeError(f'exchange listing map too small: {len(out)}; failures={fails}')
    return out,src,'上交所/深交所官方股票列表（退市列表为尽力补充）'

def listing_dates_eastmoney():
    hosts=['https://push2.eastmoney.com/api/qt/clist/get','https://82.push2.eastmoney.com/api/qt/clist/get','https://20.push2.eastmoney.com/api/qt/clist/get','https://48.push2.eastmoney.com/api/qt/clist/get','https://7.push2.eastmoney.com/api/qt/clist/get']
    base={'pz':100,'po':1,'np':1,'fltt':2,'invt':2,'fid':'f12','fs':'m:0+t:6,m:0+t:80,m:0+t:81+s:2048,m:1+t:2,m:1+t:23,m:1+t:8','fields':'f12,f14,f26'}
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Referer':'https://quote.eastmoney.com/','Accept':'application/json,text/plain,*/*','Connection':'close'}
    out={};src={}; total_pages=60
    time.sleep(random.uniform(.5,3))
    for pn in range(1,total_pages+1):
        ok=False;last=None
        for i in range(14):
            try:
                p=dict(base);p['pn']=pn;url=hosts[(pn+i)%len(hosts)]
                with requests.Session() as s:
                    r=s.get(url,params=p,headers=h,timeout=25);r.raise_for_status();j=r.json()
                data=j.get('data') or {};diff=data.get('diff') or []
                if pn==1:
                    total=int(data.get('total') or 0);total_pages=max(1,min(80,(total+99)//100));logging.info('Eastmoney total=%d pages=%d',total,total_pages)
                if not diff and pn<=total_pages: raise RuntimeError('empty diff')
                for x in diff:
                    c=str(x.get('f12') or '');d=norm_date(x.get('f26') or '')
                    if valid_code(c) and d:out[c]=d;src[c]='东方财富上市日期兜底'
                ok=True;break
            except Exception as e:
                last=e;time.sleep(min(15,1.2*(2**min(i,4)))+random.random())
        if not ok: raise RuntimeError(f'Eastmoney page {pn} failed: {last!r}')
        if pn>=total_pages:break
        time.sleep(random.uniform(.05,.2))
    if len(out)<3500: raise RuntimeError(f'Eastmoney listing map too small: {len(out)}')
    return out,src,'东方财富兜底'

def listing_dates():
    try:
        out,src,source=listing_dates_exchange();logging.info('listing map from exchanges=%d',len(out));return out,src,source
    except Exception as e:
        logging.warning('exchange listing source insufficient, use Eastmoney fallback: %r',e)
        out,src,source=listing_dates_eastmoney();return out,src,source

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();y=a.year;inp=a.input_dir;out=a.output;out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    lm,lmsrc,lmsource=listing_dates(); cutoff=f'{y}1231'
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
        rr['上市日期']=d;rr['上市日期来源']=lmsrc.get(c,'')
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
        c=s['证券代码']; rs=g.get(c,[]);found=c in rcodes;op=oldp.get(c,{})
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
    summary={'报告年度':y,'上市日期数据源':lmsource,'上市日期映射代码数':len(lm),'原样本公司数':sum(1 for r in sample if str(r.get('最终是否纳入样本'))=='1'),'剔除年末尚未上市公司数':len(removed),'上市日期缺失代码数':len(set(missing_date)),
             '修正后最终样本公司数':len(kept),'年报覆盖公司数':len(rcodes),'年报覆盖率（%）':cov,'严格银行任职记录数':len(det),'有银行背景公司数_严格':len({r.get('证券代码') for r in det}),'人工复核记录数_严格':len(rev),'错误记录数':len(errors)}
    write_csv(out/'01_样本公司清单_上市日期修正.csv',corrected);write_csv(out/'02_年报清单_最终.csv',sorted(reports,key=lambda r:r.get('证券代码','')))
    write_csv(out/'03_高管银行任职分段明细_严格.csv',det);write_csv(out/'04_人工复核队列_严格.csv',rev);write_csv(out/'05_公司年度高管银行背景面板_严格.csv',panel);write_csv(out/'99_错误日志.csv',errors)
    (out/'00_运行摘要_上市日期修正.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
