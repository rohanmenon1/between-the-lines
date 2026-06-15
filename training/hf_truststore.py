import sys

import truststore


truststore.inject_into_ssl()

from huggingface_hub.cli.hf import main


if __name__ == "__main__":
    sys.exit(main())
