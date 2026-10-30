"""CLI: one UTF-8 JSON response on stdout, diagnostic JSON lines on stderr."""
import argparse
import logging
from pathlib import Path
import sys

from .contracts import RequestError, error_response, serialize, parse_and_validate
from .runtime import handle_request, read_clock
from .storage import Store


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal exit would bypass the uniform JSON error contract.
        raise RequestError("INVALID_REQUEST", "Invalid CLI arguments; use --help for usage.")


def exit_code(response: dict) -> int:
    if response["status"] == "succeeded":
        return 0
    if response["status"] == "unknown":
        return 4
    if response["error"]["code"] in {"STORAGE_BUSY", "STORAGE_ERROR", "SETTLEMENT_FAILED", "INTERNAL_ERROR"}:
        return 3
    return 2


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = Parser(description="Local validation, replay and logical clock foundation.", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False, help="Execute a validated JSON write request")
    run.add_argument("--db", required=True)
    run.add_argument("--input", required=True)
    clock = commands.add_parser("clock", allow_abbrev=False, help="Read logical time without modifying it")
    clock.add_argument("--db", required=True)
    confirm=commands.add_parser('confirm',allow_abbrev=False,help='User explicitly accepts one exact draft version, slot and risks')
    confirm.add_argument('--db',required=True); confirm.add_argument('--input',required=True)
    drafts=commands.add_parser('drafts',allow_abbrev=False,help='Read current task drafts')
    drafts.add_argument('--db',required=True); drafts.add_argument('--task-id',required=True)
    agent=commands.add_parser('agent',allow_abbrev=False,help='Run the bounded agent; mock is offline flow verification only')
    agent.add_argument('--db',required=True); agent.add_argument('--task-id',required=True)
    agent.add_argument('--model',required=True,choices=['mock','bailian'])
    agent.add_argument('--trace',required=True); agent.add_argument('--max-steps',type=int,default=24)
    try:
        args = parser.parse_args(argv)
        if args.command == "clock":
            response = read_clock(Store(args.db))
        elif args.command == 'drafts':
            from .business import read_drafts
            response=read_drafts(Store(args.db),args.task_id)
        elif args.command == 'agent':
            from .agent import run_agent
            from .model import OfflineMock
            if args.model=='bailian':
                from .bailian import BailianModel
                model=BailianModel()
            else: model=OfflineMock()
            response=run_agent(Store(args.db),args.task_id,model,args.trace,max_steps=args.max_steps)
            print(serialize(response))
            if any(i['save_status']=='unknown' for i in response['items']): return 4
            if any(i['item_status']=='save_failed' for i in response['items']): return 3
            return 0 if response['task_status']=='completed' else 2
        else:
            try:
                raw = Path(args.input).read_text(encoding="utf-8")
            except (OSError, ValueError):
                raise RequestError("INVALID_REQUEST", "Input file must be accessible UTF-8 JSON.", "input") from None
            if args.command=='confirm':
                if parse_and_validate(raw).operation!='draft.confirm':
                    raise RequestError('INVALID_REQUEST','The confirm entry accepts only draft.confirm.')
                response=handle_request(raw,Store(args.db),channel='user')
            else:
                response = handle_request(raw, Store(args.db))
    except RequestError as exc:
        response = error_response(exc)
    print(serialize(response))
    return exit_code(response)


if __name__ == "__main__":
    raise SystemExit(main())
