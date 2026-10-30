"""Independent process client used to verify persistence and real lock contention."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from schedule_agent.runtime import handle_request
from schedule_agent.storage import Store
from schedule_agent.contracts import serialize

if __name__ == "__main__":
    print("ready", flush=True)
    sys.stdin.readline()
    print(serialize(handle_request(sys.argv[2], Store(Path(sys.argv[1])))), flush=True)
