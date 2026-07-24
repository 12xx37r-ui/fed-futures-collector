from collector import main as collect
from engine.run_engine import main as run_engine
from backtest.snapshot import main as snapshot
from backtest.evaluate import main as evaluate

if __name__ == "__main__":
    collect()
    run_engine()
    snapshot()
    evaluate()
