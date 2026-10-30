"""VS Code F5 / Run Python File entry. Defaults to a free offline demonstration."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from schedule_agent.agent import run_agent
from schedule_agent.contracts import RequestError, error_response
from schedule_agent.model import OfflineMock
from schedule_agent.runtime import handle_request
from schedule_agent.storage import Store


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='直接运行素材排期演示；默认 Mock，不调用付费模型。')
    parser.add_argument('--model',choices=['mock','bailian'],default='mock')
    parser.add_argument('--output-root',type=Path,default=ROOT/'var')
    args=parser.parse_args()
    try:
        if args.model=='bailian':
            from schedule_agent.bailian import BailianModel
            model=BailianModel()  # Shared project budget; never reset for a demo.
        else: model=OfflineMock()
        folder=args.output_root.resolve()/('demo-'+uuid4().hex)
        folder.mkdir(parents=True,exist_ok=False)
        db=folder/'state.sqlite3'; trace=folder/'trace.jsonl'; output=folder/'result.json'
        store=Store(db)
        for name in ('initialize.json','business/upgrade.json','business/load-conflict.json','business/task.json'):
            response=handle_request((ROOT/'examples'/name).read_text(encoding='utf-8'),store)
            if response['status']!='succeeded':
                print(json.dumps(response,ensure_ascii=False,indent=2))
                return 2
        result=run_agent(store,'DEMO',model,trace,max_steps=10)
        report=dict(model_mode=model.mode,notice='只保存草稿；待确认时退出码 2 属于正常业务结果。',
                    database_path=str(db),trace_path=str(trace),output_path=str(output),result=result)
        text=json.dumps(report,ensure_ascii=False,indent=2)
        output.write_text(text+'\n',encoding='utf-8')
        print(text)
        if any(i['save_status']=='unknown' for i in result['items']): return 4
        if any(i['item_status']=='save_failed' for i in result['items']): return 3
        return 0 if result['task_status']=='completed' else 2
    except RequestError as exc:
        print(json.dumps(error_response(exc),ensure_ascii=False,indent=2))
        return 2
    except OSError:
        print(json.dumps({'status':'failed','error':{'code':'DEMO_IO_ERROR',
              'message':'Cannot read or write demo files. Inspect the run directory for any committed draft; do not assume rollback.'}}))
        return 3


if __name__=='__main__': raise SystemExit(main())
