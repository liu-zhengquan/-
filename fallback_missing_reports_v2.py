#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, csv, json, logging, random, re, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from repair_executive_bank_history import read_csv, write_csv, strict_clean_row, dedupe_details, parse_report
from crawl_executive_bank_history import PerStockCninfo, clean, EXCLUDE_TITLE, CNINFO_PDF


def query(cli, year, code, name, column, mode):
    out=[]
    # mode: stock-code only, no-stock+name search, no-stock+code search, loose title/category
    if mode==0:
        stock=code; key=''; cat='category_ndbg_szsh;'
    elif mode==1:
        stock=''; key=name; cat='category_ndbg_szsh;'
    elif mode==2:
        stock=''; key=code; cat='category_ndbg_szsh;'
    else:
        stock=''; key=name; cat=''
    for page in range(1,11):
        data={"pageNum":page,"pageSize":30,"column":column,"tabName":"fulltext","plate":"","stock":stock,
              "searchkey":key,"secid":"","category":cat,"trade":"","seDate":f"{year+1}-01-01~{year+3}-12-31",
              "sortName":"","sortType":"","isHLtitle":"true"}
        try:j=cli.post(data)
        except Exception:break
        anns=j.get('announcements') or []
        if not anns:break
        for a in anns:
            sc=str(a.get('secCode') or '')
            title=re.sub(r'<.*?>','',str(a.get('announcementTitle') or ''))
            ct=clean(title)
            if sc!=code: continue
            # accept standard and punctuation/space variants, but still require annual report and target year
            if str(year) not in ct or '年度报告' not in ct: continue
            if any(x in title for x in EXCLUDE_TITLE): continue
            adj=str(a.get('adjunctUrl') or '')
            if not adj.lower().endswith('.pdf'): continue
            out.append({"报告年度":year,"证券代码":code,"证券简称":str(a.get('secName') or name),"年报标题":title,
                        "公告时间":int(a.get('announcementTime') or 0),"公告ID":str(a.get('announcementId') or ''),
                        "数据来源链接":CNINFO_PDF+adj.lstrip('/'),"交易所查询列":column,"补查方式":f'v2_mode{mode}'})
        if len(anns)<30:break
        time.sleep(.1+random.random()*.2)
    return out


def find_one(year,row):
    code=row['证券代码']; name=row.get('证券简称','')
    own='sse' if code.startswith('6') else 'szse'; other='szse' if own=='sse' else 'sse'
    cli=PerStockCninfo(); allr=[]
    for mode in range(4):
        for col in (own,other):
            allr.extend(query(cli,year,code,name,col,mode))
            if allr:break
        if allr:break
    if not allr:return None
    def score(x):return (int('修订' in x['年报标题'] or '更新' in x['年报标题']),int('更正' in x['年报标题']),x['公告时间'])
    return max(allr,key=score)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();y=a.year;inp=a.input_dir;out=a.output;out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    sample=read_csv(inp/'01_样本公司清单_剔除ST金融业.csv')
    repfile=inp/'02_年报清单_最终补查.csv'
    reports=read_csv(repfile)
    detfile=inp/'03_高管银行任职分段明细_严格.csv'; strict_old=read_csv(detfile)
    kept=[r for r in sample if str(r.get('最终是否纳入样本'))=='1'];by={r['证券代码']:r for r in reports};missing=[r for r in kept if r['证券代码'] not in by]
    new=[];errors=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut={ex.submit(find_one,y,r):r for r in missing}
        for i,f in enumerate(as_completed(fut),1):
            r=fut[f]
            try:
                x=f.result()
                if x:new.append(x)
            except Exception as e:errors.append({'报告年度':y,'证券代码':r['证券代码'],'阶段':'v2补查','错误':repr(e)})
            if i%50==0:logging.info('%s v2 %d/%d new=%d',y,i,len(missing),len(new))
    for r in new:by[r['证券代码']]=r
    raw_new=[];parsed_new=set()
    with tempfile.TemporaryDirectory() as td:
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut={ex.submit(parse_report,m,td):m for m in new}
            for f in as_completed(fut):
                m=fut[f]
                try:raw_new.extend(f.result());parsed_new.add(m['证券代码'])
                except Exception as e:errors.append({'报告年度':y,'证券代码':m['证券代码'],'阶段':'v2_PDF','错误':repr(e),'数据来源链接':m.get('数据来源链接','')})
    strict=list(strict_old)
    for r in raw_new:
        x=strict_clean_row(r)
        if x:strict.append(x)
    strict=dedupe_details(strict);reviews=[r for r in strict if str(r.get('是否需人工复核'))=='1']
    g=defaultdict(list)
    for r in strict:g[r['证券代码']].append(r)
    parsed_old={r['证券代码'] for r in reports};parsed=parsed_old|parsed_new
    panel=[]
    for r in kept:
        code=r['证券代码'];rs=g.get(code,[]);found=code in by;ok=code in parsed
        panel.append({'报告年度':y,'证券代码':code,'证券简称':r.get('证券简称',''),'年报是否找到':1 if found else 0,'年报是否成功解析':1 if ok else 0,
                      '是否存在高管银行关联':(1 if rs else 0) if ok else '','银行背景高管人数':len({x['高管姓名'] for x in rs}) if ok else '',
                      '关联银行数量':len({x['标准化银行名称'] for x in rs}) if ok else '',
                      '是否存在历史银行任职':1 if ok and any(str(x.get('是否历史银行任职'))=='1' for x in rs) else (0 if ok else ''),
                      '是否存在当前银行联结':1 if ok and any(str(x.get('是否当前银行联结'))=='1' for x in rs) else (0 if ok else ''),
                      '银行背景高管名单':'；'.join(sorted({x['高管姓名'] for x in rs})) if ok else '',
                      '关联银行列表':'；'.join(sorted({x['标准化银行名称'] for x in rs})) if ok else '',
                      '样本口径':'沪深A股；剔除当年ST/退市整理及当年金融业；未覆盖公司不编码为0'})
    write_csv(out/'01_样本公司清单_剔除ST金融业.csv',sample);write_csv(out/'02_年报清单_最终补查V2.csv',sorted(by.values(),key=lambda r:r['证券代码']))
    write_csv(out/'03_高管银行任职分段明细_严格.csv',strict);write_csv(out/'04_人工复核队列_严格.csv',reviews);write_csv(out/'05_公司年度高管银行背景面板_严格.csv',panel);write_csv(out/'99_错误日志.csv',errors)
    summary={'报告年度':y,'最终样本公司数':len(kept),'V2补查前年报覆盖数':len(reports),'V2补查后年报覆盖数':len(by),'V2补查后年报覆盖率（%）':round(100*len(by)/len(kept),2),'V2新增年报数':len(new),'严格银行任职记录数':len(strict),'有银行背景公司数_严格':len({r['证券代码'] for r in strict}),'人工复核记录数_严格':len(reviews),'错误记录数':len(errors)}
    (out/'00_运行摘要_最终补查V2.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
