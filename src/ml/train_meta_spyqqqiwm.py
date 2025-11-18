import sys
from pathlib import Path

from src.core.config_loader import load_config
from src.ml.train_model import main as train_main


def run():
    """
    Convenience wrapper to train a SPY+QQQ+IWM meta model using the existing trainer.
    Usage:
      python -m src.ml.train_meta_spyqqqiwm --config config/config.yaml
    """
    # Rewrite argv to pass symbols explicitly
    argv = sys.argv
    # If no symbols provided, add default trio
    if "--symbols" not in argv:
        argv = argv + ["--symbols", "SPY", "QQQ", "IWM"]
    sys.argv = argv
    train_main()


if __name__ == "__main__":
    run()


