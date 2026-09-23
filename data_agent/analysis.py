import html,json
from .catalog import METRICS

def analyze(artifact,kind='table'):
    if artifact['is_truncated']:raise ValueError('结果已截断，拒绝对预览进行总体分析，请缩小查询范围')
    rows=artifact['rows'];metric=METRICS[artifact['contract']['metric']]
    values=[r['value'] for r in rows if r.get('value') is not None]
    stats={'rows':len(rows),'defined_values':len(values),'min':min(values) if values else None,'max':max(values) if values else None}
    # No misleading sum of daily distinct values or mean of ratios.
    text=f"{metric['name']}：取得 {len(rows)} 行；实际统计范围 {artifact['effective_start']} 至 {artifact['effective_end']}。"
    if values:text+=f"各行指标值范围为 {min(values):.4g} 至 {max(values):.4g}（{metric['unit']}）。"
    else:text+='没有可计算的指标值；请检查筛选范围或分母。'
    summary={'text':text,'stats':stats,'method':'固定函数：行数、非空计数、最小值和最大值；不进行跨粒度汇总',
             'metric_definition':metric['rule'],'warnings':artifact.get('warnings',[]),
             'evidence':{'artifact_id':artifact['id'],'sql':artifact['sql'],'contract':artifact['contract'],'watermark':artifact['watermark'],'snapshot':artifact['snapshot']}}
    if kind in ('bar','line'):summary['chart_svg']=chart(rows,kind,metric['name'])
    summary['markdown']=f"# {metric['name']}\n\n{text}\n\n口径：{metric['rule']}\n\n分析方法：{summary['method']}\n\n数据水位：{artifact['watermark']}\n\n结果 ID：{artifact['id']}\n\n注意：{'；'.join(summary['warnings']) or '描述性分析，不代表因果结论。'}\n\n```sql\n{artifact['sql']}\n```\n"
    return summary

def chart(rows,kind,title):
    limit=2000 if kind=='line' else 200
    if len(rows)>limit:raise ValueError(f'图表超过 {limit} 个点，请缩小日期或维度；不会静默截取')
    if kind=='line' and rows and 'date' not in rows[0]:raise ValueError('趋势图缺少日期维度，需要补充查询')
    width,height=880,380;pad=55
    esc=lambda x:html.escape(str(x),quote=True)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',f'<text x="55" y="27" font-size="17" fill="#182b43">{esc(title)}</text>']
    if not rows:return ''.join(parts+['<text x="55" y="80">无数据</text></svg>'])
    maxv=max([r['value'] for r in rows if r.get('value') is not None]+[1e-9]);plot_h=height-2*pad
    colors=['#147d92','#7655c5','#cf7337','#408853','#b84d71']
    for i in range(5):
        y=height-pad-i*plot_h/4
        parts.append(f'<path d="M {pad} {y} H {width-pad}" stroke="#e3e8ee"/><text x="4" y="{y}" font-size="11">{maxv*i/4:.3g}</text>')
    if kind=='line':
        dates=sorted({r['date'] for r in rows});groups={}
        for r in rows:
            key=' / '.join(str(r[k]) for k in ['channel','os'] if k in r) or '总体'
            groups.setdefault(key,{})[r['date']]=r['value']
        for j,(key,items) in enumerate(groups.items()):
            color=colors[j%len(colors)];points=[]
            for i,d in enumerate(dates):
                v=items.get(d)
                if v is None:
                    if points:parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>');points=[]
                    continue
                x=pad+i*(width-2*pad)/max(1,len(dates)-1);y=height-pad-v/maxv*plot_h;points.append(f'{x:.1f},{y:.1f}')
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{color}"><title>{esc(key)} {d}: {v:.4g}</title></circle>')
            if points:parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<text x="{pad+j*155}" y="{height-12}" fill="{color}" font-size="11">{esc(key)}</text>')
        parts.append(f'<text x="{pad}" y="{height-35}" font-size="11">{dates[0]} → {dates[-1]}</text>')
    else:
        step=(width-2*pad)/len(rows)
        for i,r in enumerate(rows):
            if r.get('value') is None:continue
            label=' / '.join(str(r[k]) for k in ['date','channel','os'] if k in r) or '总体';v=r['value'];h=v/maxv*plot_h
            parts.append(f'<rect x="{pad+i*step+2:.1f}" y="{height-pad-h:.1f}" width="{max(.5,step-4):.1f}" height="{h:.1f}" fill="{colors[i%len(colors)]}"><title>{esc(label)}: {v:.4g}</title></rect>')
            if len(rows)<=12:parts.append(f'<text x="{pad+i*step:.1f}" y="{height-pad+20}" font-size="10">{esc(label)}</text>')
    parts.append('</svg>');return ''.join(parts)
