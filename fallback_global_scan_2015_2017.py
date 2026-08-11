#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse,csv,json,logging,re,tempfile,time,random,calendar
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from repair_executive_bank_history import read_csv,write_csv,strict_clean_row,dedupe_details,parse_report
from crawl_executive_bank_history import PerStockCninfo,clean,EXCLUDE_TITLE,CNINFO_PDF


def month_windows(year):
    out=[]
    for yy in [year+1,year+2]:
        maxm=12 if yy==year+1 else 8
        for m in range(1,maxm+1):
            last=calendar.monthrange(yy,m)[1]
            out.append((f'{yy}-{m:02d}-01',f'{yy}-{m:02d}-{last:02d}'))
    return out


def query_window(cli,year,column,start,end):
    rows=[]; page=1
    while page<=200:
        data={"pageNum":page,"pageSize":30,"column":column,"tabName":"fulltext","plate":"","stock":"","searchkey":"","secid":"",
              "category":"category_ndbg_szsh;","trade":"","seDate":f"{start}~{end}","sortName":"","sortType":"","isHLtitle":"true"}
        j=cli.post(data); anns=j.get('announcements') or []
        if not anns: break
        for a in anns:
            code=str(a.get('secCode') or '')
            if not re.fullmatch(r'\d{6}',code): continue
            title=re.sub(r'<.*?>','',str(a.get('announcementTitle') or ''))
            ct=clean(title)
            if f'{year}年年度报告' not in ct and f'{year}年度报告' not in ct: continue
            if any(x in title for x in EXCLUDE_TITLE): continue
            adj=str(a.get('adjunctUrl') or '')
            if not adj.lower().endswith('.pdf'): continue
            rows.append({'报告年度':year,'证券代码':code,'证券简称':str(a.get('secName') or ''),'年报标题':title,
                         '公告时间':int(a.get('announcementTime') or 0),'公告ID':str(a.get('announcementId') or ''),
                         '数据来源链接':CNINFO_PDF+adj.lstrip('/'),'交易所查询列':column})
        total=int(j.get('totalpages') or j.get('totalPages') or 0)
        if j.get('hasMore') is False or (total and page>=total) or len(anns)<30: break
        page+=1; time.sleep(.08+random.random()*.08)
    return rows


def best_report(rows):
    def score(x): return (int('修订' in x['年报标题'] or '更新' in x['年报标题']),int('更正' in x['年报标题']),x['公告时间'])
    b={}
    for r in rows:
        c=r['证券代码']
        if c not in b or score(r)>score(b[c]): b[c]=r
    return b


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();y=a.year;inp=a.input_dir;out=a.output;out.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    sample=read_csv(inp/'01_样本公司清单_剔除ST金融业.csv')
    report_file=inp/'02_年报清单_最终补查.csv'
    reports=read_csv(report_file if report_file.exists() else inp/'02_年报清单_修复后.csv')
    raw_file=inp/'03A_高管银行候选_清洗前.csv'
    raw=read_csv(raw_file) if raw_file.exists() else []
    strict_old=read_csv(inp/'03_高管银行任职分段明细_严格.csv') if (inp/'03_高管银行任职分段明细_严格.csv').exists() else []
    kept=[r for r in sample if str(r.get('最终是否纳入样本'))=='1']; by={r['证券代码']:r for r in reports}; missing={r['证券代码'] for r in kept if r['证券代码'] not in by}
    cli=PerStockCninfo(); found=[]; errors=[]
    for col in ['szse','sse']:
        for i,(s,e) in enumerate(month_windows(y),1):
            try: rr=query_window(cli,y,col,s,e); found.extend(rr)
            except Exception as ex: errors.append({'报告年度':y,'阶段':'全市场月度扫描','交易所':col,'日期窗':s+'~'+e,'错误':repr(ex)})
            logging.info('%s %s window %d found=%d',y,col,i,len(found))
            time.sleep(.2+random.random()*.2)
    global_best=best_report(found); new=[]
    for c in missing:
        if c in global_best: new.append(global_best[c]); by[c]=global_best[c]
    logging.info('%s missing=%d global matched=%d',y,len(missing),len(new))
    raw_new=[]; parsed_new=set()
    with tempfile.TemporaryDirectory() as td:
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut={ex.submit(parse_report,m,td):m for m in new}
            for f in as_completed(fut):
                m=fut[f]
                try: raw_new.extend(f.result()); parsed_new.add(m['证券代码'])
                except Exception as ex: errors.append({'报告年度':y,'证券代码':m['证券代码'],'阶段':'全市场补查_PDF','错误':repr(ex),'数据来源链接':m.get('数据来源链接','')})
    strict=[]
    if raw:
        for r in raw+raw_new:
            x=strict_clean_row(r)
            if x: strict.append(x)
        strict=dedupe_details(strict)
    else:
        strict=dedupe_details(strict_old)
        for r in raw_new:
            x=strict_clean_row(r)
            if x: strict.append(x)
        strict=dedupe_details(strict)
    reviews=[r for r in strict if str(r.get('是否需人工复核'))=='1']
    g=defaultdict(list)
    for r in strict:g[r['证券代码']].append(r)
    parsed_old={r['证券代码'] for r in reports}; parsed=parsed_old|parsed_new
    panel=[]
    for r in kept:
        code=r['证券代码'];rs=g.get(code,[]);foundr=code in by;ok=code in parsed
        panel.append({'报告年度':y,'证券代码':code,'证券简称':r.get('证券简称',''),'年报是否找到':1 if foundr else 0,'年报是否成功解析':1 if ok else 0,
                      '是否存在高管银行关联':(1 if rs else 0) if ok else '', '银行背景高管人数':len({x['高管姓名'] for x in rs}) if ok else '',
                      '关联银行数量':len({x['标准化银行名称'] for x in rs}) if ok else '', '是否存在历史银行任职':1 if ok and any(str(x.get('是否历史银行任职'))=='1' for x in rs) else (0 if ok else ''),
                      '是否存在当前银行联结':1 if ok and any(str(x.get('是否当前银行联结'))=='1' for x in rs) else (0 if ok else ''),
                      '银行背景高管名单':'；'.join(sorted({x['高管姓名'] for x in rs})) if ok else '', '关联银行列表':'；'.join(sorted({x['标准化银行名称'] for x in rs})) if ok else '',
                      '样本口径':'沪深A股；剔除当年ST/退市整理及当年金融业；未覆盖公司不编码为0'})
    write_csv(out/'01_样本公司清单_剔除ST金融业.csv',sample);write_csv(out/'02_年报清单_全市场补查.csv',sorted(by.values(),key=lambda r:r['证券代码']))
    write_csv(out/'03_高管银行任职分段明细_严格.csv',strict);write_csv(out/'04_人工复核队列_严格.csv',reviews);write_csv(out/'05_公司年度高管银行背景面板_严格.csv',panel);write_csv(out/'99_错误日志.csv',errors)
    summary={'报告年度':y,'最终样本公司数':len(kept),'补查前年报覆盖数':len(reports),'全市场扫描新增年报数':len(new),'补查后年报覆盖数':len(by),'补查后年报覆盖率（%）':round(100*len(by)/len(kept),2),
             '严格银行任职记录数':len(strict),'有银行背景公司数_严格':len({r['证券代码'] for r in strict}),'人工复核记录数_严格':len(reviews),'错误记录数':len(errors)}
    (out/'00_运行摘要_全市场补查.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
