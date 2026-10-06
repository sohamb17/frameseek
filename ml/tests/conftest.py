import os

import pytest

needs_db = pytest.mark.skipif(not os.environ.get("FRAMESEEK_TEST_DATABASE_URL"),
                              reason="set FRAMESEEK_TEST_DATABASE_URL to run database tests")
