"""Render charts and an operations-style report from saved simulation outcomes."""
import csv
import html
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def read_csv(name):
    with (ROOT / 'results' / name).open(encoding='utf-8') as file:
        return list(csv.DictReader(file))


def render():
    summary, runs = read_csv('summary.csv'), read_csv('runs.csv')
    metadata = json.loads((ROOT / 'results/environment.json').read_text())
    charts = ROOT / 'docs/charts'
    charts.mkdir(parents=True, exist_ok=True)
    scenarios = ['healthy', 'crash_window', 'downstream_outage', 'short_visibility', 'budget_cutoff']
    labels = ['Healthy + poison', 'Crash before ack', '12 s outage', 'Lease too short', '5 s budget cutoff']
    find = lambda scenario, policy='atomic_backoff': next(r for r in summary if r['scenario'] == scenario and r['policy'] == policy)
    value = lambda s, p, m: float(find(s, p)[m])
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'figure.facecolor':'#f5f7ef',
                         'axes.facecolor':'#f5f7ef','axes.spines.top':False,'axes.spines.right':False,
                         'axes.spines.left':False,'axes.edgecolor':'#c7d1c4','axes.labelcolor':'#264538',
                         'xtick.color':'#264538','ytick.color':'#264538','savefig.dpi':160})
    def export(fig, name):
        fig.text(.01,.012,'Discrete-event simulation · 30 seeds per scenario/policy · 120 jobs per run · logical seconds',fontsize=8,color='#557060')
        fig.savefig(charts / f'{name}.png', bbox_inches='tight')
        fig.savefig(charts / f'{name}.svg', bbox_inches='tight')
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(12,5.3))
    x=np.arange(len(scenarios));width=.34
    for offset,policy,color,label in [(-width/2,'naive_backoff','#ad694d','No effect deduplication'),(width/2,'atomic_backoff','#1c6549','Atomic effect + ledger')]:
        vals=[value(s,policy,'mean_duplicate_effects') for s in scenarios]
        bars=ax.bar(x+offset,vals,width,label=label,color=color)
        ax.bar_label(bars,fmt='%.1f',padding=4,fontsize=10)
    ax.set_xticks(x,labels);ax.set_ylabel('Mean duplicate effects per 120-job run')
    ax.set_ylim(0,360);ax.set_title('Deduplicating an effect does not deduplicate delivery',loc='left',fontsize=20,fontweight='bold',pad=18)
    ax.legend(frameon=False,loc='upper left');fig.tight_layout(rect=(0,.07,1,1));export(fig,'01_duplicate_effects')

    fig,ax=plt.subplots(figsize=(12,6))
    rows=[find(s) for s in scenarios]+[find('downstream_outage','atomic_immediate')]
    names=labels+['12 s outage / immediate retry'];left=np.zeros(len(rows))
    for key,color,label in [('mean_acked','#1c6549','Acknowledged'),('mean_dlq','#ad694d','Dead-lettered'),('mean_pending_at_budget','#b7c8b8','Still pending at budget')]:
        vals=np.array([float(r[key]) for r in rows]);ax.barh(names,vals,left=left,color=color,label=label,height=.58)
        for i,v in enumerate(vals):
            if v>=8:ax.text(left[i]+v/2,i,f'{v:.1f}',ha='center',va='center',color='white' if key!='mean_pending_at_budget' else '#17372a',fontsize=11)
        left+=vals
    ax.invert_yaxis();ax.set_xlim(0,120);ax.set_xlabel('Mean jobs per run · each row sums to 120')
    ax.set_title('Broker disposition is separate from side-effect state',loc='left',fontsize=20,fontweight='bold',pad=18)
    ax.legend(frameon=False,ncol=3,loc='upper center',bbox_to_anchor=(.5,-.12),fontsize=9)
    fig.tight_layout(rect=(0,.1,1,1));export(fig,'02_job_accounting')

    fig,axes=plt.subplots(1,2,figsize=(13,5.6))
    for ax,metric,title in [(axes[0],'mean_first_delivery_lag_p95','First-delivery queue lag'),(axes[1],'mean_ack_latency_p95','Completion latency among acknowledged jobs')]:
        vals=[float(find(s)[metric]) if find(s)[metric] else np.nan for s in scenarios]
        ax.barh(labels,vals,color='#1c6549',height=.56);ax.invert_yaxis()
        ax.set_title(title,loc='left',fontsize=14,fontweight='bold',pad=18)
        ax.set_xlabel('Mean of per-run p95 values · logical seconds')
        for i,v in enumerate(vals):
            if np.isnan(v):ax.text(1.5,i,'No acknowledgements — undefined',va='center',fontsize=9,color='#92563e')
            else:ax.text(v+.4,i,f'{v:.1f}',va='center',fontsize=9)
        ax.set_xlim(0,max(v for v in vals if not np.isnan(v))*1.25)
    fig.tight_layout(rect=(0,.07,1,1));export(fig,'03_logical_latency')

    trace_name='crash_window--naive_backoff--seed0.jsonl'
    naive=[json.loads(l) for l in (ROOT/'results/traces'/trace_name).read_text().splitlines()]
    atomic=[json.loads(l) for l in (ROOT/'results/traces/crash_window--atomic_backoff--seed0.jsonl').read_text().splitlines()]
    traced_job=next(r['job'] for r in naive if r['event']=='crashed_before_ack')
    plotted={'delivered':('o','#1c6549'),'effect_applied':('s','#ad694d'),'effect_deduplicated':('D','#7e9562'),
             'crashed_before_ack':('X','#7c3735'),'lease_expired':('v','#687d6b'),'ack_accepted':('*','#173c2c')}
    fig,ax=plt.subplots(figsize=(13,5.6))
    legend=set()
    for lane,records in enumerate([naive,atomic]):
        selected=[r for r in records if r.get('job')==traced_job and r['event'] in plotted]
        ax.plot([min(r['time'] for r in selected),max(r['time'] for r in selected)],[lane,lane],color='#b5c2b0',lw=2,zorder=1)
        for i,r in enumerate(selected):
            marker,color=plotted[r['event']]
            label=r['event'].replace('_',' ') if r['event'] not in legend else None
            ax.scatter(r['time'],lane,s=85,marker=marker,color=color,label=label,zorder=3)
            legend.add(r['event'])
    ax.set_yticks([0,1],['Naive effects','Atomic ledger']);ax.invert_yaxis();ax.set_ylim(1.6,-.6)
    ax.set_xlabel('Logical time (seconds)');ax.set_title(f'Captured trace: {traced_job} / seed 0',loc='left',fontsize=20,fontweight='bold',pad=18)
    ax.legend(frameon=False,ncol=3,loc='upper center',bbox_to_anchor=(.5,-.17),fontsize=9)
    fig.tight_layout(rect=(0,.15,1,1));export(fig,'04_crash_trace')

    crash=find('crash_window','naive_backoff');outage=find('downstream_outage');leases=find('short_visibility')
    trace_rows=[r for r in naive if r.get('job')==traced_job and r['event'] in plotted]
    trace_table=''.join(f"<tr><td>{r['time']:.3f}</td><td>{html.escape(r['event'])}</td><td>{r.get('attempt',r.get('receipt','—'))}</td><td>{r.get('effect_count','—')}</td></tr>" for r in trace_rows)
    table=''.join(f"<tr><td>{labels[i]}</td><td>{float(find(s)['mean_acked']):.1f}</td><td>{float(find(s)['mean_dlq']):.1f}</td><td>{float(find(s)['mean_pending_at_budget']):.1f}</td><td>{float(find(s)['mean_dlq_with_effect']):.1f}</td><td>{find(s)['drained_runs']}/30</td></tr>" for i,s in enumerate(scenarios))
    report='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Job reliability lab</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:85px}body{margin:0;background:#f5f7ef;color:#173a2b;font:16px/1.6 Segoe UI,Arial,sans-serif}header{position:sticky;top:0;z-index:2;background:#123c2b;color:#eff6e9;display:flex;justify-content:space-between;padding:18px 5%;gap:20px}header b{font:13px Consolas,monospace;letter-spacing:.1em}header a{font-size:13px;color:#e1edda;text-decoration:none;margin-left:18px}main{max-width:1300px;padding:0 50px;margin:auto}section{padding:56px 0;border-bottom:1px solid #c9d4c5;scroll-margin-top:30px}.tag{font:12px Consolas,monospace;letter-spacing:.12em;text-transform:uppercase;color:#54735a}h1{font-size:65px;line-height:1.07;letter-spacing:-2.4px;max-width:960px;margin:18px 0 26px}h2{font-size:35px;letter-spacing:-1px;line-height:1.2;margin:14px 0 24px}h3{font-size:20px;margin-bottom:10px}.lead{font-size:21px;max-width:960px;color:#4c6652}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:3px solid #1c6549;border-bottom:1px solid #cad5c5;margin:34px 0}.stats div{padding:20px 10px}.stats strong{display:block;font-size:34px;font-weight:500}.stats span{font-size:13px;color:#52705a}.notice{background:#e1ebda;border-left:4px solid #1c6549;padding:16px 20px;margin:24px 0}.warning{border-left-color:#ad694d;background:#eee6d8}.chart{width:100%;height:auto;display:block;margin:22px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:30px}.steps{padding:0;list-style:none;counter-reset:step}.steps li{padding:18px 20px;border-top:1px solid #c8d3c3;counter-increment:step}.steps li:before{content:counter(step,decimal-leading-zero);font:15px Consolas,monospace;display:inline-block;width:40px;color:#669170}pre{background:#123c2b;color:#edf6e5;padding:22px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 Consolas,monospace;border-radius:3px}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #cad5c5}th{color:#5b755f;font-size:12px;text-transform:uppercase}a{color:#246949}code{font:14px Consolas,monospace}footer{padding:26px 0 55px;font-size:13px;color:#627a65}@media(max-width:850px){main{padding:0 22px}header nav{display:none}h1{font-size:43px}.grid{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}}@media print{header{position:static}section{break-inside:avoid}}
</style></head><body><header><b>JOB RELIABILITY LAB / SIMULATION</b><nav><a href="#overview">Overview</a><a href="#delivery">Delivery</a><a href="#retries">Retries</a><a href="#leases">Leases</a><a href="#traces">Traces</a><a href="#runbook">Runbook</a></nav></header><main>'''
    report+=f'''<section id="overview"><div class="tag">01 / simulation results</div><h1>The job ran.<br>The acknowledgement<br>never arrived.</h1><p class="lead">A discrete-event model of at-least-once job delivery: crash windows, expiring leases, bounded retries and atomic effect deduplication.</p><div class="stats"><div><strong>450</strong><span>seeded scenario / policy runs</span></div><div><strong>30</strong><span>seeds per comparison</span></div><div><strong>{float(crash['mean_duplicate_effects']):.1f}</strong><span>mean duplicate effects / crash run, naive</span></div><div><strong>0</strong><span>duplicate effects with modeled atomic ledger</span></div></div><p class="notice">All times and failures are simulated. No live broker, cloud deployment or production throughput is claimed. Atomic deduplication assumes the effect and ledger entry share one transaction boundary.</p><img class="chart" src="charts/01_duplicate_effects.png" alt="Duplicate effects under naive and atomic policies across five scenarios"></section>
<section id="delivery"><div class="tag">02 / delivery and effect accounting</div><h2>An acknowledgement is a broker state.<br>An effect is application state.</h2><div class="grid"><ol class="steps"><li>Worker receives a job with a new receipt token.</li><li>The application applies the effect.</li><li>A crash can occur before acknowledgement.</li><li>The lease expires; the broker retries the job.</li><li>An atomic ledger suppresses a repeated effect.</li></ol><div><pre>transaction:\n  if key exists:\n    reject mismatched payload\n    skip repeated effect\n  apply modeled side effect\n  store key + payload\ncommit\n\nack(current_receipt)</pre><p>This transaction boundary is a deliberate model assumption. A separate database insert followed by an external HTTP request does not satisfy it.</p></div></div><img class="chart" src="charts/02_job_accounting.png" alt="Acknowledged, dead-lettered and pending job counts"><table><thead><tr><th>Atomic / backoff scenario</th><th>Acked</th><th>DLQ</th><th>Pending</th><th>DLQ with effect</th><th>Drained runs</th></tr></thead><tbody>{table}</tbody></table></section>
<section id="retries"><div class="tag">03 / retry budget</div><h2>Immediate retries can exhaust attempts before recovery.</h2><p>In the configured 12-second downstream outage, atomic workers with immediate retry acknowledge zero jobs: all 120 exhaust six attempts. Exponential backoff with full jitter acknowledges a mean <strong>{float(outage['mean_acked']):.1f} of 120</strong>. The remaining mean {float(outage['mean_dlq']):.1f} jobs reach the DLQ without an effect.</p><div class="grid"><pre>delay = uniform(0, min(cap, base × 2^(attempt − 1)))\nbase = 1 logical second\ncap = 16 logical seconds\nmaximum attempts = 6</pre><div><p>Backoff also increases completion time. It does not make poison work succeed or guarantee recovery before the attempt budget runs out.</p><p>Random draws are indexed by seed, job, attempt and purpose. Changing scheduling does not shift another job's failure draw. Outage failures intentionally depend on logical completion time.</p></div></div><img class="chart" src="charts/03_logical_latency.png" alt="Queue lag and acknowledged-only latency in logical seconds"><p class="notice warning">Completion latency excludes jobs without an acknowledgement. An undefined p95 is shown explicitly. The budget-cutoff mean uses 29 nonempty runs; the full CSV records the denominator.</p></section>
<section id="leases"><div class="tag">04 / visibility timeout failure</div><h2>A stale worker may finish after the broker has given up.</h2><p>The short-lease scenario sets visibility to 0.6 seconds while processing takes 2–4 seconds. No receipt survives until completion. Every job reaches the DLQ after four deliveries; a mean <strong>{float(leases['mean_dlq_with_effect']):.1f} jobs per run already have an effect</strong>.</p><div class="grid"><div><h3>Lease expiry</h3><ul><li>Invalidates only the matching receipt.</li><li>Makes the job eligible for retry.</li><li>Does not free or cancel the busy worker.</li><li>Late acknowledgements are rejected.</li></ul></div><div><h3>Drain accounting</h3><ul><li>A broker-terminal job can still have a running worker.</li><li>Effects after a DLQ transition remain visible in the trace.</li><li>Budget-pending jobs are not classified as lost or failed.</li><li>Drain requires zero pending jobs and zero executing tasks.</li></ul></div></div><p class="notice">Idempotency prevents repeated modeled effects; it does not repair a lease that always expires too early. Fit visibility to processing duration or implement lease renewal in a real worker design.</p><p>Same-time ordering is explicit: expiry precedes completion, then retry/arrival readiness, then worker restart. A completion exactly at lease expiry has a stale receipt in this model.</p></section>
<section id="traces"><div class="tag">05 / replayable evidence</div><h2>Follow one crash through both policies.</h2><img class="chart" src="charts/04_crash_trace.png" alt="Actual simulated event trace for one job under naive and atomic policies"><div class="grid"><table><thead><tr><th>Logical s</th><th>Naive event</th><th>Attempt</th><th>Effect count</th></tr></thead><tbody>{trace_table}</tbody></table><div><pre>python scripts/replay_trace.py \\\n  results/traces/crash_window--naive_backoff--seed0.jsonl</pre><p>The replay auditor reads event records and recomputes acknowledgements, DLQ transitions and effect counts without running the scheduler. It rejects inconsistent terminal events and altered summaries.</p><p><a href="../results/traces/">Seven JSONL traces</a> · <a href="../results/runs.csv">All 450 run outcomes</a> · <a href="../results/environment.json">Configurations and seeds</a></p></div></div></section>
<section id="runbook"><div class="tag">06 / operational response</div><h2>Inspect the failure before replaying the queue.</h2><div class="grid"><ol class="steps"><li>Check pending, executing, acknowledged and DLQ counts separately.</li><li>Compare lease duration with worker processing time.</li><li>Inspect DLQ entries for an existing application effect.</li><li>Check idempotency-key scope and payload mismatch policy.</li><li>Bound retries and confirm downstream recovery before redrive.</li></ol><div><pre>python scripts/run_experiments.py\npython scripts/render_report.py\npython -m unittest discover -s tests -v</pre><p><a href="runbook.md">Detailed runbook</a> · <a href="architecture-decision.md">Architecture decision</a> · <a href="../README.md">Repository guide</a></p><p>AI-assisted engineering study. These are reproducible simulations, not an implementation of a production broker or proof of exactly-once delivery. The model omits partitions, persistent broker recovery, lease renewal and external side-effect transactions.</p></div></div></section><footer>Generated from recorded simulation outcomes · Executed {metadata['executed_at_utc']} · Logical seconds throughout.</footer></main></body></html>'''
    from report_layout import responsive
    report = responsive(report)
    (ROOT/'docs/report.html').write_text(report,encoding='utf-8')
    print('Rendered four chart pairs and six-section static report')


if __name__=='__main__':
    render()
