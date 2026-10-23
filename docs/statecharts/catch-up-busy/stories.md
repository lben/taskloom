## 1. After the check: 6:31 running, 6:32 skipped, 6:33 waiting its turn

Rule broken: Catch-up does not skip the oldest minute of a backlog while a run is going.
Why: 6:31 is still running, so catch-up skips 6:32 and only queues 6:33.
This is the only way to get here in the model.

0. Start: Before the check: 6:31 running, 6:32 still due, 6:33 still due
1. The check skips 6:32. Now: After the check: 6:31 running, 6:32 skipped, 6:33 waiting its turn

With the fix, step 1 (the check skips 6:32) takes you to: After the check: 6:31 running, 6:32 waiting its turn, 6:33 waiting its turn.
