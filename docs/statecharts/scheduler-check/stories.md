## 1. Catch up: 6:31 running, 6:32 waiting its turn, 6:33 skipped

Rule broken: Catch-up keeps every minute in the check while the scheduler is still running.
Why: Catch-up starts 6:31 and queues 6:32, then skips 6:33 because that minute is still a few seconds away.
There are 5 ways to get here in the model. This is the shortest.

0. Start: Catch up: waiting for the check
1. The check skips 6:33. Now: Catch up: 6:31 running, 6:32 waiting its turn, 6:33 skipped

With the fix, step 1 (the check skips 6:33) takes you to: Catch up: 6:31 running, 6:32 waiting its turn, 6:33 waiting its turn.

## 2. Catch up: 6:31 running, 6:32 dropped, 6:33 dropped, scheduler is off

Rule broken: A run that was accepted is not thrown away when the scheduler stops.
Why: The scheduler stops while a catch-up run is only in memory, and that run is gone.
There are 3 ways to get here in the model. This is the shortest.

0. Start: Catch up: 6:31 running, 6:32 waiting its turn, 6:33 waiting its turn
1. The scheduler stops and drops a waiting run. Now: Catch up: 6:31 running, 6:32 dropped, 6:33 dropped, scheduler is off

With the fix, step 1 (the scheduler stops and drops a waiting run) takes you to: Catch up: 6:31 running, 6:32 waiting its turn, 6:33 waiting its turn, scheduler is off.

## 3. Run once: 6:31 counted as missed, 6:32 running, 6:33 skipped

Rule broken: Run once keeps the newest minute and does not start an older one.
Why: The check a few seconds early starts 6:32 and skips 6:33, so run once loses the newest minute.
This is the only way to get here in the model.

0. Start: Run once: waiting for the check
1. The check skips 6:33. Now: Run once: 6:31 counted as missed, 6:32 running, 6:33 skipped

With the fix, step 1 (the check skips 6:33) takes you to: Run once: 6:31 counted as missed, 6:32 counted as missed, 6:33 running.

## 4. Skip missed runs: 6:31 counted as missed, 6:32 running, 6:33 skipped

Rule broken: Skip counts every earlier minute as missed.
Why: The early check runs 6:32 and counts only 6:31 as missed, so one missed minute is never counted.
This is the only way to get here in the model.

0. Start: Skip missed runs: waiting for the check
1. The check skips 6:33. Now: Skip missed runs: 6:31 counted as missed, 6:32 running, 6:33 skipped

With the fix, step 1 (the check skips 6:33) takes you to: Skip missed runs: 6:31 counted as missed, 6:32 counted as missed, 6:33 running.

## 5. Skip missed runs: 6:31 counted as missed, 6:32 running, 6:33 skipped

Rule broken: Skip starts the on-time minute.
Why: The check a few seconds early starts 6:32 and skips 6:33, the minute that was on time.
This is the only way to get here in the model.

0. Start: Skip missed runs: waiting for the check
1. The check skips 6:33. Now: Skip missed runs: 6:31 counted as missed, 6:32 running, 6:33 skipped

With the fix, step 1 (the check skips 6:33) takes you to: Skip missed runs: 6:31 counted as missed, 6:32 counted as missed, 6:33 running.
