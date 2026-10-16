"""Resumable local batch maintenance. Does not call Claude or modify source books."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Library


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'.data'))
    parser.add_argument('--mode',choices=['missing','errors','metadata'],default='missing')
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args();lib=Library(Path(args.data_dir))
    while args.wait and lib.classifier.progress()['running']:time.sleep(10)
    result=lib.classifier.start(mode=args.mode)
    print(json.dumps(result,ensure_ascii=True),flush=True)
    if not result['started']:return 1
    while lib.classifier.state['running']:
        time.sleep(10);print(json.dumps(lib.classifier.state,ensure_ascii=True),flush=True)
    summary=lib.classifier.summary()
    (lib.classifier.directory/'batch-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)
    return 0 if lib.classifier.state['done']==lib.classifier.state['total'] else 1


if __name__=='__main__':raise SystemExit(main())
