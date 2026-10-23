## 1. Block returned a value: attempt 2 running

Rule broken: A value returned during the grace period is the attempt's result.
Why: The block finished during the timeout grace and the engine threw that result away.
There are 3 ways to get here in the model. This is the shortest.

0. Start: Block in progress: attempt 1 running
1. The block returns a value during the grace period. Now: Block returned a value: attempt 1 returned during the grace period
2. The engine throws that result away. Now: Block returned a value: attempt 2 running

With the fix, step 2 (the engine throws that result away) takes you to: Block returned a value: kept as success.
