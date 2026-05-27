"""Allow `python -m almc_shield` invocation."""
from almc_shield.main import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
