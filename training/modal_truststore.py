"""Run Modal CLI with Windows trust store certificates.

This is a local Windows helper for environments where antivirus or corporate
TLS inspection presents certificates that Python's bundled certifi store does
not trust, but the OS trust store does.
"""

import truststore


truststore.inject_into_ssl()

from modal.__main__ import main  # noqa: E402


if __name__ == "__main__":
    main()
