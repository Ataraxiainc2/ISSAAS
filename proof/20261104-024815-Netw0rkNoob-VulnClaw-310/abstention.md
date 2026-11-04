# Abstention — Netw0rkNoob/VulnClaw#310

**Issue:** [Bug]: --stream 收尾对 verified_claims 做切片崩溃：get_summary() 返回计数（int），任务成功即异常退出

ISSA AS could not produce a verified fix.

**Reason:** Step budget exhausted (final forced step, no tool calls allowed): the regression test tests/cli/test_stream_complete_regression.py is committed (d593b43) and the build_stream_findings helper exists in vulnclaw/cli/_helpers.py, but the isinstance list guard at both --stream tails in vulnclaw/cli/main.py (run ~1468, solve ~1609) was never applied and no green test run was observed. No source fix committed; the bug is not fixed.
